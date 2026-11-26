from datetime import date
import json
import logging
import sqlite3
from threading import Event
from time import monotonic
from uuid import uuid4

from corp_docs_agent.persistence.store import Store, corpus_revision, json_dump, now, visible_versions
from corp_docs_agent.providers.llama import Chat, Decision, ProviderError
from corp_docs_agent.retrieval.search import (
    Candidate, amount_applies, eligible_versions, expand_evidence, lexical_ids, load_candidates, rank_ids,
    search_terms, select_evidence, topic_supported,
)
from corp_docs_agent.retrieval.terms import canonical_question


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
        terms = search_terms(question["question"], turns, context)
        with self.store.connect() as conn:
            visible = visible_versions(conn, question["role"])
            revision = corpus_revision(conn)
            versions, missing = eligible_versions(
                visible, date.fromisoformat(question["as_of"]), context, json.loads(question["document_ids"]),
            )
            lexical = lexical_ids(conn, terms, versions)
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
            candidates = select_evidence(primary, adjacent)
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
        if candidates and topic_supported(
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
                        ON CONFLICT(root_id) DO UPDATE SET question_id = excluded.question_id
                        WHERE quality_cases.status = 'pending'""",
                        (case_id, question["root_id"], question["id"], now()),
                    )
                    result["quality_case_id"] = conn.execute(
                        "SELECT id FROM quality_cases WHERE root_id = ?", (question["root_id"],),
                    ).fetchone()[0]
            result["usage"]["latency_ms"] = elapsed_ms(started)
            conn.execute(
                "UPDATE questions SET status = 'completed', result = ? WHERE id = ?",
                (json_dump(result), question["id"]),
            )
