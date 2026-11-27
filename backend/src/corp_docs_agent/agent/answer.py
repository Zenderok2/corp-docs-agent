from datetime import date
import json
import logging
import sqlite3
from threading import Event
from time import monotonic
from uuid import uuid4

from corp_docs_agent.agent.grounding import (
    concise_excerpts, outside_document_qa, table_abbreviations_covered, verification_input,
)
from corp_docs_agent.persistence.quality_mail import send_request
from corp_docs_agent.persistence.store import Store, corpus_revision, json_dump, now, visible_versions
from corp_docs_agent.providers.llama import Chat, ProviderError
from corp_docs_agent.retrieval.search import (
    Candidate, amount_applies, eligible_versions, expand_evidence, lexical_ids, load_candidates, load_numbered_clauses,
    mentioned_departments, rank_ids, search_terms, select_evidence, topic_supported,
)
from corp_docs_agent.retrieval.terms import canonical_question, glossary_hints, scope_key


logger = logging.getLogger(__name__)
MAX_FOLLOW_UPS = 2
COVERAGE_BUDGET_SECONDS = 30.0
SUMMARY_BUDGET_SECONDS = 60.0
SCOPE_QUESTIONS = {
    "legal_entity": "О каком юридическом лице идёт речь?",
    "department": "О каком подразделении идёт речь?",
    "product": "Для какого продукта нужен ответ?",
    "license_type": "Какой тип лицензии рассматривается?",
}


def elapsed_ms(started: float) -> int:
    return round((monotonic() - started) * 1000)


def add_chat_usage(usage: dict, call_usage: dict) -> None:
    usage["chat_calls"] += 1
    if "chat_model" in call_usage:
        usage["chat_model"] = call_usage["chat_model"]
    if not {"prompt_tokens", "completion_tokens"}.issubset(call_usage):
        usage["unmetered_chat_calls"] = usage.get("unmetered_chat_calls", 0) + 1
    for key in ("prompt_tokens", "completion_tokens"):
        if key in call_usage:
            usage[key] = usage.get(key, 0) + call_usage[key]


def locate_evidence(
    refs: list[int], sources: list[Candidate], introductions: dict[str, Candidate] | None = None,
) -> list[tuple[Candidate, int, int]]:
    located: dict[str, Candidate] = {}
    for ref in refs:
        if not 1 <= ref <= len(sources):
            raise ProviderError("unverified_evidence")
        source = sources[ref - 1]
        if introductions and (intro := introductions.get(source.id)):
            located.setdefault(intro.id, intro)
        located.setdefault(source.id, source)
    return [(source, source.start, source.start + len(source.text)) for source in located.values()]


def scope_passages(field: str, question: str, candidates: list[Candidate], semantic_support: set[str]) -> dict[str, list[Candidate]]:
    options: dict[str, list[Candidate]] = {}
    for item in candidates:
        if set(item.missing_scope) != {field} or not topic_supported(
            question, [item.text], semantic_match=item.id in semantic_support,
        ):
            continue
        label = item.missing_scope[field]
        variant = options.setdefault(label, [])
        if len(variant) < 2 and all(previous.block_id != item.block_id for previous in variant):
            variant.append(item)
    return options


def decide_scope(evidence: list, candidates: list[Candidate], context: dict) -> tuple[str | None, list[str]]:
    selected_versions = {candidate.version.id for candidate, _, _ in evidence}
    # A scoped successor or related exception can change a selected general rule.
    selected_links = {
        ref for candidate, _, _ in evidence for ref in candidate.version.metadata["related_to"]
    }
    missing = [candidate.missing_scope for candidate in candidates if (
        candidate.version.id in selected_versions or candidate.version.id in selected_links
        or selected_versions.intersection(candidate.version.metadata["supersedes"])
    )]
    for key in SCOPE_QUESTIONS:
        choices = sorted({scope[key] for scope in missing if key in scope})
        if not context.get(key) and choices:
            return key, choices
    return None, []


def find_quality_answer(conn: sqlite3.Connection, question: sqlite3.Row) -> sqlite3.Row | None:
    key = canonical_question(question["question"])
    matches = conn.execute(
        """
        SELECT k.id, k.canonical_question, k.answer, k.reviewed_at
        FROM quality_keys keys
        JOIN quality_cases k ON k.id = keys.case_id
        JOIN questions source ON source.id = k.question_id
        WHERE keys.question_key = ? AND k.status = 'published'
          AND source.role = ? AND source.context = ? AND source.document_ids = ?
          AND source.turns = ? AND source.as_of <= ?
          AND (k.effective_to IS NULL OR k.effective_to >= ?)
        ORDER BY k.reviewed_at DESC, k.id DESC LIMIT 2
        """,
        (key, question["role"], question["context"], question["document_ids"], question["turns"],
         question["as_of"], question["as_of"]),
    ).fetchall()
    if not matches or (len(matches) > 1 and matches[0]["answer"] != matches[1]["answer"]):
        return None
    return matches[0]


class AnswerWorker:
    def __init__(self, store: Store, chat: Chat, vectors=None):
        self.store = store
        self.chat = chat
        self.vectors = vectors
        self.wakeup = Event()
        self.stopped = Event()

    def run(self) -> None:
        while not self.stopped.is_set():
            self.wakeup.clear()
            try:
                question = self.store.claim_question()
                if question is not None:
                    self.process(question)
                    continue
            except sqlite3.Error:
                logger.warning("Question queue could not access SQLite")
            self.wakeup.wait(0.5)

    def process(self, question: sqlite3.Row) -> None:
        started = monotonic()
        try:
            result, evidence = self.answer(question)
            self.publish(question, result, evidence, started)
        except Exception as error:
            code = str(error) if isinstance(error, ProviderError) else "processing_failed"
            logger.warning("Question processing failed (%s)", code)
            with self.store.connect(write=True) as conn:
                conn.execute(
                    "UPDATE questions SET status = 'failed', result = ? WHERE id = ?",
                    (json_dump({"error_code": code, "latency_ms": elapsed_ms(started)}), question["id"]),
                )

    def answer(self, question: sqlite3.Row) -> tuple[dict, list]:
        started = monotonic()
        context = json.loads(question["context"])
        turns = json.loads(question["turns"])
        combined = " ".join([question["question"], *turns])
        as_of = date.fromisoformat(question["as_of"])
        document_ids = json.loads(question["document_ids"])
        with self.store.connect() as conn:
            visible = visible_versions(conn, question["role"])
            revision = corpus_revision(conn)
            if outside_document_qa(question["question"]):
                return {
                    "outcome": "out_of_scope", "message_code": "out_of_scope", "as_of": question["as_of"],
                    "corpus_revision": revision, "clarification": None, "trace": [],
                    "usage": {"chat_calls": 0, "embedding_calls": 0}, "department_refs": {}, "finding_refs": [],
                }, []
            if not context.get("department") and not context.get("departments"):
                available, _ = eligible_versions(visible, as_of, context, document_ids)
                labels = {version.metadata["scope"]["department"] for version in available.values()
                          if version.metadata["scope"].get("department")}
                mentioned = mentioned_departments(combined, labels)
                if len(mentioned) == 1:
                    context["department"] = mentioned[0]
                elif 1 < len(mentioned) <= 5:
                    context["departments"] = mentioned
            terms = search_terms(question["question"], turns, context)
            department_versions = {}
            if departments := context.get("departments"):
                versions, missing = {}, {}
                for department in departments:
                    member_versions, member_missing = eligible_versions(
                        visible, as_of, {**context, "department": department}, document_ids,
                    )
                    department_versions[department] = member_versions
                    versions.update(member_versions)
                    missing.update(member_missing)
            else:
                versions, missing = eligible_versions(visible, as_of, context, document_ids)
            lexical = lexical_ids(conn, terms, versions)
            anchors = {
                department: lexical_ids(conn, terms, {
                    key: item for key, item in member_versions.items() if item.metadata["scope"].get("department")
                }, limit=3)
                for department, member_versions in department_versions.items()
            }
        vector_ids, semantic_support, vector_usage, vector_state = [], set(), {}, None
        if self.vectors and versions and terms:
            vector_ids, semantic_support, vector_usage, vector_state = self.vectors.search(
                self.store, revision, list(versions), combined,
            )
        with self.store.connect() as conn:
            if corpus_revision(conn) != revision:
                raise ProviderError("source_access_changed")
            primary = load_candidates(conn, rank_ids(lexical, vector_ids), versions, missing)
            primary = [item for item in primary if amount_applies(combined, item.text)]
            adjacent = expand_evidence(conn, primary, versions, missing, terms)
            adjacent = [item for item in adjacent if amount_applies(combined, item.text)]
            anchor_candidates = load_candidates(conn, [key for ids in anchors.values() for key in ids], versions, missing)
            anchor_candidates = [item for item in anchor_candidates if amount_applies(combined, item.text)]
            first_by_department = {}
            for item in anchor_candidates:
                first_by_department.setdefault(scope_key(item.version.metadata["scope"]["department"]), item)
            ranked = primary + anchor_candidates
            clauses = load_numbered_clauses(conn, ranked + adjacent, versions, missing, combined)
            candidates = select_evidence(ranked, adjacent, list(first_by_department.values()), clauses)
        # A list item inherits the scope and qualifications in its introductory paragraph.
        introductions = {
            item.id: group[0] for item in candidates
            if (group := clauses.get(item.block_id)) and group[0].id != item.id and group[0].text.rstrip().endswith(":")
        }
        trace = [{
            "step": "retrieval", "lexical_hits": len(lexical), "vector_hits": len(vector_ids),
            "related_hits": len(adjacent), "candidates": len(candidates), "vector_state": vector_state,
            "duration_ms": elapsed_ms(started),
        }]
        usage = {"chat_calls": 0, "embedding_calls": 0, **vector_usage}
        outcome = "not_found"
        message_code = "no_evidence" if visible else "empty_corpus"
        evidence: list = []
        clarification: dict | None = None
        review_partial = False
        field, variants = None, {}
        for name in ("legal_entity", "department"):
            topical = scope_passages(name, question["question"], primary, semantic_support)
            if len(topical) > 1:
                field, variants = name, topical
                break
        department_refs = {}
        if field:
            options = sorted(variants)
            for option in options:
                items = variants[option]
                if field == "department":
                    department_refs[option] = list(range(len(evidence), len(evidence) + len(items)))
                evidence.extend((item, item.start, item.start + len(item.text)) for item in items)
            outcome = "needs_clarification"
            clarification = {"question": SCOPE_QUESTIONS[field], "field": field, "options": options}
        elif candidates and topic_supported(
            question["question"], [item.topic_text() for item in candidates],
            semantic_match=any(item.id in semantic_support for item in candidates),
        ):
            model_started = monotonic()
            assessment = {
                "question": question["question"],
                "clarification_answers": turns,
                "as_of": question["as_of"],
                "glossary": glossary_hints(combined),
                "context": context,
                "sources": [
                    {
                        "ref": number, "title": item.version.metadata["title"],
                        "version": item.version.metadata["version"], "status": item.version.publication_status_on(as_of),
                        "missing_scope": item.missing_scope, "text": item.text,
                        "scope": {field: value for field, value in item.version.metadata["scope"].items() if value},
                        **{key: item.locator[key] for key in ("section_path", "clause_number", "column_header")
                           if item.locator.get(key)},
                        **({"clause_intro": introductions[item.id].text} if item.id in introductions else {}),
                    }
                    for number, item in enumerate(candidates, 1)
                ],
            }
            if department_versions:
                outcome, evidence, clarifier, department_refs = self.assess_departments(
                    assessment, candidates, department_versions, semantic_support, usage, introductions,
                    started + getattr(self.chat, "timeout", 90.0),
                )
            else:
                decision, chat_usage = self.chat.decide(assessment)
                add_chat_usage(usage, chat_usage)
                outcome, clarifier = decision.outcome, decision.clarification
                review_partial = outcome == "partial"
                if outcome != "not_found":
                    evidence = locate_evidence(
                        decision.evidence, candidates, introductions if outcome in {"answered", "partial"} else None,
                    )
            trace.append({"step": "assessment", "duration_ms": elapsed_ms(model_started)})
            if outcome == "answered" and not topic_supported(
                question["question"],
                [source.topic_text() for source, _, _ in evidence],
                semantic_match=any(source.id in semantic_support for source, _, _ in evidence),
            ):
                outcome, evidence = "not_found", []
            if outcome in {"answered", "partial", "conflict"} and not evidence:
                outcome = "not_found"
            if outcome == "conflict" and len({source.block_id for source, _, _ in evidence}) < 2:
                outcome = "partial"
            field, options = decide_scope(evidence, candidates, context)
            if field:
                clarification = {"question": SCOPE_QUESTIONS[field], "field": field, "options": options}
                outcome = "needs_clarification"
            elif outcome == "needs_clarification":
                clarifier = " ".join(clarifier.split())
                if clarifier.endswith("?"):
                    clarification = {"question": clarifier, "field": None, "options": []}
                else:
                    outcome = "partial" if evidence else "not_found"
        if department_versions and not department_refs:
            department_refs = {department: [] for department in department_versions}
        if outcome == "needs_clarification" and len(turns) >= MAX_FOLLOW_UPS:
            outcome = "partial" if evidence else "not_found"
            clarification = None
            message_code = "clarification_exhausted"
        elif outcome in {"answered", "partial"} and any(
            source.version.publication_status_on(as_of) != "published" for source, _, _ in evidence
        ):
            outcome = "partial"
            message_code = "unknown_publication"
        elif outcome != "not_found":
            message_code = outcome
        findings = []
        verification_started = monotonic()
        if message_code == "partial" and review_partial:
            deadline = started + getattr(self.chat, "timeout", 90.0)
            confirmed = self.confirm_coverage(question["question"], context, evidence, candidates, usage, deadline)
            if confirmed:
                evidence = confirmed
                outcome = message_code = "answered"
        if message_code in {"answered", "partial", "conflict"}:
            findings = concise_excerpts(evidence)
            if evidence and not findings and not any(source.locator.get("column_header") for source, _, _ in evidence):
                deadline = started + min(getattr(self.chat, "timeout", 90.0), SUMMARY_BUDGET_SECONDS)
                findings = self.summarize(question["question"], evidence, department_refs, usage, deadline)
        trace.append({
            "step": "verification", "verified_citations": len(evidence), "verified_findings": len(findings),
            "duration_ms": elapsed_ms(verification_started),
        })
        return {
            "outcome": outcome, "message_code": message_code, "as_of": question["as_of"],
            "corpus_revision": revision, "clarification": clarification, "trace": trace, "usage": usage,
            "department_refs": department_refs,
            "finding_refs": findings,
        }, evidence

    def confirm_coverage(
        self, question: str, context: dict, evidence: list[tuple[Candidate, int, int]],
        candidates: list[Candidate], usage: dict, deadline: float,
    ) -> list[tuple[Candidate, int, int]] | None:
        remaining = deadline - monotonic()
        if remaining < 3 or not evidence:
            return None
        sources = [source for source, _, _ in evidence]
        if any(source.missing_scope for source in sources) or not topic_supported(question, [item.topic_text() for item in sources]):
            return None
        timeout = min(COVERAGE_BUDGET_SECONDS, remaining)
        if any(source.locator.get("column_header") for source in sources):
            return self.confirm_table_coverage(question, context, sources, candidates, usage, timeout)
        if not hasattr(self.chat, "check_coverage"):
            return None
        checks = {
            "question": question, "context": context,
            "citations": [{"text": source.text, "column_header": source.locator.get("column_header"),
                           "scope": source.version.metadata["scope"]} for source in sources],
        }
        try:
            coverage, call_usage = self.chat.check_coverage(checks, timeout)
        except ProviderError:
            add_chat_usage(usage, {})
            return None
        add_chat_usage(usage, call_usage)
        return evidence if coverage.complete else None

    def confirm_table_coverage(
        self, question: str, context: dict, sources: list[Candidate], candidates: list[Candidate],
        usage: dict, timeout: float,
    ) -> list[tuple[Candidate, int, int]] | None:
        anchor = sources[0]
        path = anchor.locator.get("table_path")
        if not path or not hasattr(self.chat, "check_table_coverage"):
            return None
        cells = [
            item for item in candidates
            if item.version.id == anchor.version.id and item.locator.get("column_header")
            and item.locator.get("table_path", [])[:-1] == path[:-1]
        ]
        original_ids = {source.id for source in sources}
        if not original_ids.issubset(item.id for item in cells):
            return None
        checks = {
            "question": question, "context": context,
            "cells": [{"ref": ref, "text": item.text, "column_header": item.locator["column_header"]}
                      for ref, item in enumerate(cells, 1)],
        }
        try:
            coverage, call_usage = self.chat.check_table_coverage(checks, timeout)
        except ProviderError:
            add_chat_usage(usage, {})
            return None
        add_chat_usage(usage, call_usage)
        if not coverage.complete:
            return None
        try:
            reviewed = locate_evidence(coverage.evidence, cells)
        except ProviderError:
            return None
        selected = [item for item, _, _ in reviewed]
        if not original_ids.issubset(item.id for item in selected):
            return None
        return reviewed if table_abbreviations_covered(question, cells, selected) else None

    def assess_departments(
        self, assessment: dict, candidates: list[Candidate], department_versions: dict,
        semantic_support: set[str], usage: dict, introductions: dict[str, Candidate], deadline: float,
    ) -> tuple[str, list, str, dict[str, list[int]]]:
        outcomes = []
        evidence = []
        citation_positions = {}
        groups = {department: [] for department in department_versions}
        clarifier = ""
        failure = None
        for number, (department, versions) in enumerate(department_versions.items()):
            scope_matches = [
                (item, source) for item, source in zip(candidates, assessment["sources"], strict=True)
                if item.version.id in versions
            ][:5]
            if not topic_supported(
                assessment["question"], [item.topic_text() for item, _ in scope_matches],
                semantic_match=any(item.id in semantic_support for item, _ in scope_matches),
            ):
                outcomes.append("not_found")
                continue
            remaining = deadline - monotonic()
            if remaining < 3:
                failure = ProviderError("model_timeout")
                break
            context = {key: value for key, value in assessment["context"].items() if value and key != "departments"}
            context["department"] = department
            try:
                decision, call_usage = self.chat.decide({
                    **assessment, "context": context, "focus_department": department,
                    "sources": [{**source, "ref": ref} for ref, (_, source) in enumerate(scope_matches, 1)],
                }, timeout=remaining / (len(department_versions) - number))
                selected = [] if decision.outcome == "not_found" else locate_evidence(
                    decision.evidence, [item for item, _ in scope_matches],
                    introductions if decision.outcome in {"answered", "partial"} else None,
                )
            except ProviderError as error:
                add_chat_usage(usage, {})
                failure = error
                outcomes.append("not_found")
                continue
            add_chat_usage(usage, call_usage)
            outcome = decision.outcome
            if outcome == "answered" and not topic_supported(
                assessment["question"], [item.topic_text() for item, _, _ in selected],
                semantic_match=any(item.id in semantic_support for item, _, _ in selected),
            ):
                outcome, selected = "not_found", []
            if outcome == "conflict" and len({item.block_id for item, _, _ in selected}) < 2:
                outcome = "partial"
            outcomes.append(outcome)
            if outcome == "needs_clarification" and decision.clarification.endswith("?") and not clarifier:
                clarifier = decision.clarification
            for item, start, end in selected:
                if item.id not in citation_positions:
                    citation_positions[item.id] = len(evidence)
                    evidence.append((item, start, end))
                groups[department].append(citation_positions[item.id])
        if failure and not evidence:
            raise failure
        if clarifier:
            outcome = "needs_clarification"
        elif "conflict" in outcomes:
            outcome = "conflict"
        elif not failure and all(outcome == "answered" for outcome in outcomes):
            outcome = "answered"
        else:
            outcome = "partial" if evidence else "not_found"
        return outcome, evidence, clarifier, groups

    def summarize(self, question: str, evidence: list, scopes: dict, usage: dict, deadline: float) -> list[dict]:
        remaining = deadline - monotonic()
        if remaining < 10 or not hasattr(self.chat, "summarize"):
            return []
        sources = [source for source, _, _ in evidence]
        try:
            draft, draft_usage = self.chat.summarize(question, [
                {"ref": ref, "quote": item.text, "scope": item.version.metadata["scope"],
                 "departments": [name for name, refs in scopes.items() if ref - 1 in refs]}
                for ref, item in enumerate(sources, 1)
            ], min(16, remaining - 4))
        except ProviderError:
            add_chat_usage(usage, {})
            return []
        add_chat_usage(usage, draft_usage)
        proposals, checks = verification_input(draft.findings, sources, evidence, scopes)
        remaining = deadline - monotonic()
        if not proposals or remaining < 2:
            return []
        try:
            verified, verify_usage = self.chat.verify(checks, min(12, remaining))
        except ProviderError:
            add_chat_usage(usage, {})
            return []
        add_chat_usage(usage, verify_usage)
        return proposals if set(verified.supported) == {item["id"] for item in checks} else []

    def publish(self, question: sqlite3.Row, result: dict, evidence: list, started: float) -> None:
        with self.store.connect(write=True) as conn:
            if corpus_revision(conn) != result["corpus_revision"]:
                raise ProviderError("source_access_changed")
            allowed_versions = visible_versions(conn, question["role"])
            citation_ids = []
            for candidate, start, end in evidence:
                if candidate.version.id not in allowed_versions:
                    raise ProviderError("source_access_changed")
                original = conn.execute(
                    """SELECT b.text, b.version_id, c.start_offset, c.end_offset FROM chunks c
                    JOIN blocks b ON b.id = c.block_id WHERE c.id = ? AND b.id = ?""",
                    (candidate.id, candidate.block_id),
                ).fetchone()
                if (original is None or original["version_id"] != candidate.version.id
                        or (original["start_offset"], original["end_offset"]) != (start, end)
                        or original["text"][start:end] != candidate.text):
                    raise ProviderError("unverified_evidence")
                citation_id = uuid4().hex
                conn.execute(
                    "INSERT INTO citations VALUES (?, ?, ?, ?, ?)",
                    (citation_id, question["id"], candidate.id, start, end),
                )
                citation_ids.append(citation_id)
            result["citation_ids"] = citation_ids
            result["departments"] = [
                {"name": department, "citation_ids": [citation_ids[index] for index in refs]}
                for department, refs in result.pop("department_refs").items()
            ]
            result["findings"] = [
                {"text": finding["text"], "citation_ids": [citation_ids[index] for index in finding["refs"]]}
                for finding in result.pop("finding_refs")
            ]
            result["quality_case_id"] = None
            result["quality_answer_id"] = None
            if result["outcome"] in {"not_found", "partial", "conflict"}:
                quality = find_quality_answer(conn, question)
                if quality:
                    result["quality_answer_id"] = quality["id"]
                    if result["outcome"] == "not_found":
                        result.update(outcome="quality_answer", message_code="quality_answer")
                elif allowed_versions and result["message_code"] != "empty_corpus":
                    case_id = uuid4().hex
                    conn.execute(
                        """INSERT INTO quality_cases(id, root_id, question_id, status, created_at)
                        VALUES (?, ?, ?, 'pending', ?)
                        ON CONFLICT(root_id) DO NOTHING""",
                        (case_id, question["root_id"], question["id"], now()),
                    )
                    case = conn.execute(
                        "SELECT id, status, question_id FROM quality_cases WHERE root_id = ?", (question["root_id"],),
                    ).fetchone()
                    if case["question_id"] == question["id"] and case["status"] == "pending":
                        send_request(conn, case["id"], question["root_id"], question["question"], result["message_code"])
                    result["quality_case_id"] = case["id"]
            result["usage"]["latency_ms"] = elapsed_ms(started)
            conn.execute(
                "UPDATE questions SET status = 'completed', result = ? WHERE id = ?",
                (json_dump(result), question["id"]),
            )
