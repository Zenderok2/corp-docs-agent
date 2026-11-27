from datetime import date
import json
import sqlite3

from corp_docs_agent.agent.grounding import changes_explicit_deadline
from corp_docs_agent.persistence.store import CorpusVersion
from corp_docs_agent.retrieval.search import eligible_versions
from corp_docs_agent.retrieval.terms import canonical_question


def publication_block(outcome: str | None, source_revision: str | None, revision: str) -> str | None:
    if source_revision != revision:
        return "quality_corpus_changed"
    if outcome == "conflict":
        return "quality_source_conflict"
    return None


def changes_known_deadline(
    conn: sqlite3.Connection, question_id: str, as_of: date, versions: dict[str, CorpusVersion], reply: str,
) -> bool:
    citations = conn.execute(
        """SELECT b.version_id, b.text FROM citations s JOIN chunks c ON c.id = s.chunk_id
        JOIN blocks b ON b.id = c.block_id
        WHERE s.question_id = ? AND s.start_offset = 0 AND s.end_offset = length(b.text)""",
        (question_id,),
    )
    return any(
        version.applies_on(as_of) and version.publication_status_on(as_of) == "published"
        and changes_explicit_deadline(citation["text"], reply)
        for citation in citations if (version := versions.get(citation["version_id"]))
    )


def find_quality_answer(
    conn: sqlite3.Connection, question: sqlite3.Row, outcome: str, revision: str,
    versions: dict[str, CorpusVersion], *, case_id: str | None = None,
) -> sqlite3.Row | None:
    if outcome not in {"not_found", "partial"}:
        return None
    matches = conn.execute(
        """
        SELECT k.id, k.canonical_question AS question, k.answer, k.reviewed_at, source.as_of
        FROM quality_keys keys JOIN quality_cases k ON k.id = keys.case_id
        JOIN questions source ON source.id = k.question_id
        JOIN questions asked ON asked.id = ?
        WHERE keys.question_key = ? AND k.status = 'published' AND (? IS NULL OR k.id = ?)
          AND source.role = asked.role AND source.context = asked.context
          AND source.document_ids = asked.document_ids AND source.turns = asked.turns
          AND source.as_of <= asked.as_of AND (k.effective_to IS NULL OR k.effective_to >= asked.as_of)
          AND json_extract(source.result, '$.corpus_revision') = ?
          AND json_extract(source.result, '$.outcome') = ?
          AND NOT EXISTS (
              SELECT chunk_id, start_offset, end_offset FROM citations WHERE question_id = source.id
              EXCEPT SELECT chunk_id, start_offset, end_offset FROM citations WHERE question_id = asked.id
          )
          AND NOT EXISTS (
              SELECT chunk_id, start_offset, end_offset FROM citations WHERE question_id = asked.id
              EXCEPT SELECT chunk_id, start_offset, end_offset FROM citations WHERE question_id = source.id
          )
        ORDER BY k.reviewed_at DESC, k.id DESC LIMIT 2
        """,
        (question["id"], canonical_question(question["question"]), case_id, case_id, revision, outcome),
    ).fetchall()
    if not matches or (len(matches) > 1 and matches[0]["answer"] != matches[1]["answer"]):
        return None
    candidate = matches[0]
    if candidate["as_of"] != question["as_of"]:
        context = json.loads(question["context"])
        document_ids = json.loads(question["document_ids"])
        applicable = []
        for value in (candidate["as_of"], question["as_of"]):
            day = date.fromisoformat(value)
            eligible, _ = eligible_versions(versions, day, context, document_ids)
            applicable.append({key: version.publication_status_on(day) for key, version in eligible.items()})
        if applicable[0] != applicable[1]:
            return None
    if outcome == "partial" and changes_known_deadline(
        conn, question["id"], date.fromisoformat(question["as_of"]), versions, candidate["answer"],
    ):
        return None
    return candidate
