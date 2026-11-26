from datetime import date
import json
import logging
import sqlite3
from threading import Event
from time import monotonic
from uuid import uuid4

from corp_docs_agent.persistence.quality_mail import send_request
from corp_docs_agent.persistence.store import Store, corpus_revision, json_dump, now, visible_versions
from corp_docs_agent.providers.llama import Chat, Decision, ProviderError
from corp_docs_agent.retrieval.search import (
    Candidate, amount_applies, eligible_versions, expand_evidence, lexical_ids, load_candidates, mentioned_departments, rank_ids,
    search_terms, select_evidence, topic_supported,
)
from corp_docs_agent.retrieval.terms import canonical_question, scope_key


logger = logging.getLogger(__name__)
MAX_FOLLOW_UPS = 2
SCOPE_QUESTIONS = {
    "legal_entity": "О каком юридическом лице идёт речь?",
    "department": "О каком подразделении идёт речь?",
    "product": "Для какого продукта нужен ответ?",
    "license_type": "Какой тип лицензии рассматривается?",
}


def elapsed_ms(started: float) -> int:
    return round((monotonic() - started) * 1000)


def locate_evidence(decision: Decision, sources: list[Candidate]) -> list[tuple[Candidate, int, int]]:
    located = []
    for ref in dict.fromkeys(decision.evidence):
        if not 1 <= ref <= len(sources):
            raise ProviderError("unverified_evidence")
        source = sources[ref - 1]
        located.append((source, source.start, source.start + len(source.text)))
    return located


def department_passages(question: str, candidates: list[Candidate], semantic_support: set[str]) -> dict[str, list[Candidate]]:
    options: dict[str, list[Candidate]] = {}
    for item in candidates:
        if department := item.missing_scope.get("department"):
            if set(item.missing_scope) == {"department"}:
                options.setdefault(department, []).append(item)
    return {
        department: [item for item in items if topic_supported(question, [item.text], semantic_match=item.id in semantic_support)][:2]
        for department, items in options.items()
    }


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
            candidates = select_evidence(primary + anchor_candidates, adjacent, list(first_by_department.values()))
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
        variants = {
            department: items for department, items in department_passages(question["question"], primary, semantic_support).items()
            if items
        }
        department_refs = {}
        if len(variants) > 1:
            options = sorted(variants)
            for department in options:
                items = variants[department]
                department_refs[department] = list(range(len(evidence), len(evidence) + len(items)))
                evidence.extend((item, item.start, item.start + len(item.text)) for item in items)
            outcome = "needs_clarification"
            clarification = {"question": SCOPE_QUESTIONS["department"], "field": "department", "options": options}
        elif candidates and topic_supported(
            question["question"], [item.text for item in candidates],
            semantic_match=any(item.id in semantic_support for item in candidates),
        ):
            model_started = monotonic()
            decision, chat_usage = self.chat.decide({
                "question": question["question"],
                "clarification_answers": turns,
                "as_of": question["as_of"],
                "context": context,
                "sources": [
                    {
                        "ref": number, "title": item.version.metadata["title"],
                        "version": item.version.metadata["version"], "status": item.version.metadata["status"],
                        "missing_scope": item.missing_scope, "text": item.text,
                        "scope": {field: value for field, value in item.version.metadata["scope"].items() if value},
                        "applies_to_departments": [dept for dept, subset in department_versions.items() if item.version.id in subset],
                    }
                    for number, item in enumerate(candidates, 1)
                ],
            })
            usage.update(chat_usage)
            trace.append({"step": "assessment", "duration_ms": elapsed_ms(model_started)})
            outcome = decision.outcome
            if outcome != "not_found":
                evidence = locate_evidence(decision, candidates)
            if outcome == "answered" and not topic_supported(
                question["question"],
                [source.text for source, _, _ in evidence],
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
                clarifier = " ".join(decision.clarification.split())
                if clarifier and clarifier.endswith("?"):
                    clarification = {"question": clarifier, "field": None, "options": []}
                else:
                    outcome = "partial" if evidence else "not_found"
            if department_versions:
                for department, subset in department_versions.items():
                    refs = [index for index, (source, _, _) in enumerate(evidence) if source.version.id in subset]
                    scoped = [index for index in refs if evidence[index][0].version.metadata["scope"].get("department")]
                    department_refs[department] = refs
                    if outcome == "answered" and (
                        not topic_supported(question["question"], [evidence[index][0].text for index in refs],
                                            semantic_match=any(evidence[index][0].id in semantic_support for index in refs))
                        or (not scoped and scope_key(department) in first_by_department)
                    ):
                        outcome = "partial"
        if department_versions and not department_refs:
            department_refs = {department: [] for department in department_versions}
        if outcome == "needs_clarification" and len(turns) >= MAX_FOLLOW_UPS:
            outcome = "partial" if evidence else "not_found"
            clarification = None
            message_code = "clarification_exhausted"
        elif outcome == "answered" and any(source.version.metadata["status"] != "published" for source, _, _ in evidence):
            outcome = "partial"
            message_code = "unknown_publication"
        elif outcome != "not_found":
            message_code = outcome
        trace.append({"step": "verification", "verified_citations": len(evidence)})
        return {
            "outcome": outcome, "message_code": message_code, "as_of": question["as_of"],
            "corpus_revision": revision, "clarification": clarification, "trace": trace, "usage": usage,
            "department_refs": department_refs,
        }, evidence

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
