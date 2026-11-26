from datetime import date
from hashlib import sha256
import json
import sqlite3
from uuid import uuid4

from fastapi import HTTPException

from corp_docs_agent.api.auth import Principal
from corp_docs_agent.core.settings import Settings
from corp_docs_agent.persistence.quality_mail import mail_thread
from corp_docs_agent.persistence.store import corpus_revision, json_dump, now, visible_versions


def fail(status: int, code: str) -> None:
    raise HTTPException(status_code=status, detail=code)


def owned_question(conn: sqlite3.Connection, question_id: str, user: Principal) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM questions WHERE id = ? AND principal = ? AND role = ?",
        (question_id, user.id, user.role),
    ).fetchone()
    if row is None:
        fail(404, "question_not_found")
    return row


def enqueue(
    conn: sqlite3.Connection, settings: Settings, user: Principal, *,
    question: str, turns: list[str], context: dict, as_of: date, document_ids: list[str],
    parent: sqlite3.Row | None, idempotency_key: str | None, request_body: str,
) -> dict:
    request_key = idempotency_key or uuid4().hex
    parent_id = parent["id"] if parent else None
    fingerprint = sha256(json_dump([parent_id, request_body]).encode()).hexdigest()
    existing = conn.execute(
        "SELECT id, status, fingerprint, role FROM questions WHERE principal = ? AND idempotency_key = ?",
        (user.id, request_key),
    ).fetchone()
    if existing:
        if existing["role"] != user.role or existing["fingerprint"] != fingerprint:
            fail(409, "idempotency_conflict")
        return {"id": existing["id"], "status": existing["status"]}
    visible = visible_versions(conn, user.role)
    if document_ids and (set(document_ids) - {version.document_id for version in visible.values()}):
        fail(404, "document_not_found")
    if not visible:
        fail(503, "empty_corpus")
    status = conn.execute(
        """SELECT count(*) AS total, sum(principal = ?) AS owned FROM questions
        WHERE status IN ('queued', 'running')""",
        (user.id,),
    ).fetchone()
    if status["total"] >= settings.max_pending or (status["owned"] or 0) >= settings.max_pending_per_user:
        fail(429, "queue_full")
    question_id = uuid4().hex
    conn.execute(
        """INSERT INTO questions(id, principal, role, parent_id, root_id, idempotency_key, fingerprint,
        question, turns, context, document_ids, as_of, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)""",
        (question_id, user.id, user.role, parent_id, parent["root_id"] if parent else question_id,
         request_key, fingerprint, question, json_dump(turns), json_dump(context), json_dump(document_ids),
         as_of.isoformat(), now()),
    )
    return {"id": question_id, "status": "queued"}


EVIDENCE_SQL = """
SELECT s.id, s.question_id, s.chunk_id, s.start_offset, s.end_offset,
    b.id AS block_id, b.version_id, b.text, b.locator
FROM citations s JOIN chunks c ON c.id = s.chunk_id JOIN blocks b ON b.id = c.block_id
"""


def encode_citation(row: sqlite3.Row, versions: dict, *, context: bool = False) -> dict:
    version = versions[row["version_id"]]
    start, end = row["start_offset"], row["end_offset"]
    payload = {
        **version.summary(), "id": row["id"], "document_version_id": version.id,
        "chunk_id": row["chunk_id"], "block_id": row["block_id"], "locator": json.loads(row["locator"]),
        "start_offset": start, "end_offset": end, "source_hash": version.sha256,
        "quote": row["text"][start:end], "source_url": f"/api/v1/citations/{row['id']}",
    }
    if context:
        lower, upper = max(0, start - 300), min(len(row["text"]), end + 300)
        payload["context"] = {"before": row["text"][lower:start], "after": row["text"][end:upper]}
    return payload


def published_practices(conn: sqlite3.Connection, citations: list[dict], role: str) -> list[dict]:
    if not citations:
        return []
    allowed_roles = ["staff", "quality"] if role == "quality" else [role]
    rows = conn.execute(
        """SELECT p.id, p.situation, p.action_taken, p.reviewed_at, c.block_id
        FROM practices p JOIN citations s ON s.id = p.citation_id
        JOIN chunks c ON c.id = s.chunk_id JOIN questions q ON q.id = s.question_id
        WHERE p.status = 'approved' AND q.role IN (SELECT value FROM json_each(?))
          AND c.block_id IN (SELECT value FROM json_each(?))
        ORDER BY p.reviewed_at DESC, p.id LIMIT 10""",
        (json_dump(allowed_roles), json_dump([citation["block_id"] for citation in citations])),
    )
    return [dict(row) for row in rows]


def encode_question(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    payload = {
        "id": row["id"], "parent_id": row["parent_id"], "status": row["status"], "question": row["question"],
        "turns": json.loads(row["turns"]), "context": json.loads(row["context"]), "as_of": row["as_of"],
        "created_at": row["created_at"], "result": None,
    }
    if not row["result"]:
        return payload
    result = json.loads(row["result"])
    if row["status"] == "completed":
        versions = visible_versions(conn, row["role"])
        all_citations = {
            citation["id"]: citation
            for citation in conn.execute(EVIDENCE_SQL + " WHERE s.question_id = ?", (row["id"],))
        }
        citations = []
        for key in result.pop("citation_ids"):
            citation = all_citations.get(key)
            if citation is None or citation["version_id"] not in versions:
                fail(409, "source_access_changed")
            citations.append(encode_citation(citation, versions))
        result["citations"] = citations
        result.setdefault("departments", [])
        result.setdefault("findings", [])
        citation_ids = {citation["id"] for citation in citations}
        if any(not citation_ids.issuperset(item["citation_ids"]) for item in result["departments"] + result["findings"]):
            fail(409, "source_access_changed")
        result["practices"] = published_practices(conn, citations, row["role"])
        result["quality_answer"] = None
        if quality_id := result.pop("quality_answer_id"):
            quality = conn.execute(
                """SELECT id, canonical_question AS question, answer, reviewed_at
                FROM quality_cases WHERE id = ? AND status = 'published'""",
                (quality_id,),
            ).fetchone()
            if quality is None:
                fail(409, "source_access_changed")
            result["quality_answer"] = dict(quality)
        if case_id := result.pop("quality_case_id"):
            case = conn.execute("SELECT id, status, question_id FROM quality_cases WHERE id = ?", (case_id,)).fetchone()
            result["quality_case"] = {"id": case["id"], "status": case["status"], "mail": mail_thread(conn, case_id)}
            if case["status"] == "published" and case["question_id"] == row["id"] and result["quality_answer"] is None:
                quality = conn.execute(
                    "SELECT id, canonical_question AS question, answer, reviewed_at FROM quality_cases WHERE id = ?",
                    (case_id,),
                ).fetchone()
                result["quality_answer"] = dict(quality)
        else:
            result["quality_case"] = None
        result["corpus_changed"] = result["corpus_revision"] != corpus_revision(conn)
    payload["result"] = result
    return payload
