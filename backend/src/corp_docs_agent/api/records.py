from datetime import date
from hashlib import sha256
import json
import sqlite3
from uuid import uuid4

from fastapi import HTTPException

from corp_docs_agent.agent.grounding import parse_before_deadline
from corp_docs_agent.api.auth import Principal
from corp_docs_agent.core.settings import Settings
from corp_docs_agent.persistence.quality_answers import changes_known_deadline, find_quality_answer, publication_block
from corp_docs_agent.persistence.quality_mail import mail_thread
from corp_docs_agent.persistence.store import corpus_revision, json_dump, now, visible_versions


def fail(status: int, code: str) -> None:
    raise HTTPException(status_code=status, detail=code)


def owned_question(conn: sqlite3.Connection, question_id: str, user: Principal) -> sqlite3.Row:
    row = conn.execute(
        """SELECT q.*, r.chat_id FROM questions q JOIN chat_roots r ON r.root_id = q.root_id
        JOIN chats c ON c.id = r.chat_id
        WHERE q.id = ? AND q.principal = ? AND q.role = ? AND c.deleted_at IS NULL""",
        (question_id, user.id, user.role),
    ).fetchone()
    if row is None:
        fail(404, "question_not_found")
    return row


def owned_chat(conn: sqlite3.Connection, chat_id: str, user: Principal) -> sqlite3.Row:
    row = conn.execute(
        """SELECT c.id, c.title FROM chats c JOIN questions q ON q.id = c.id
        WHERE c.id = ? AND q.principal = ? AND q.role = ? AND c.deleted_at IS NULL""",
        (chat_id, user.id, user.role),
    ).fetchone()
    if row is None:
        fail(404, "chat_not_found")
    return row


def enqueue(
    conn: sqlite3.Connection, settings: Settings, user: Principal, *,
    question: str, turns: list[str], context: dict, as_of: date, document_ids: list[str],
    parent: sqlite3.Row | None, idempotency_key: str | None, request_body: str, chat_id: str | None = None,
) -> dict:
    request_key = idempotency_key or uuid4().hex
    parent_id = parent["id"] if parent else None
    fingerprint = sha256(json_dump([parent_id, request_body]).encode()).hexdigest()
    existing = conn.execute(
        """SELECT q.id, q.status, q.fingerprint, q.role, r.chat_id, c.deleted_at
        FROM questions q JOIN chat_roots r ON r.root_id = q.root_id JOIN chats c ON c.id = r.chat_id
        WHERE q.principal = ? AND q.idempotency_key = ?""",
        (user.id, request_key),
    ).fetchone()
    if existing:
        if existing["role"] != user.role or existing["fingerprint"] != fingerprint:
            fail(409, "idempotency_conflict")
        if existing["deleted_at"]:
            fail(404, "chat_not_found")
        return {"id": existing["id"], "status": existing["status"], "chat_id": existing["chat_id"]}
    if parent:
        chat_id = parent["chat_id"]
    elif chat_id:
        owned_chat(conn, chat_id, user)
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
    if not parent:
        if not chat_id:
            chat_id = question_id
            conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, question[:160]))
        conn.execute("INSERT INTO chat_roots (root_id, chat_id) VALUES (?, ?)", (question_id, chat_id))
    return {"id": question_id, "status": "queued", "chat_id": chat_id}


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


def department_comparison(departments: list[dict], citations: dict[str, sqlite3.Row]) -> list[dict]:
    if len(departments) < 2:
        return []
    comparison = []
    for department in departments:
        deadlines = []
        for citation_id in department["citation_ids"]:
            citation = citations[citation_id]
            quote = citation["text"]
            if citation["start_offset"] != 0 or citation["end_offset"] != len(quote):
                continue
            if json.loads(citation["locator"]).get("column_header"):
                continue
            if deadline := parse_before_deadline(quote):
                deadlines.append({"department": department["name"], "citation_id": citation_id, **deadline})
        if len(deadlines) != 1:
            return []
        comparison.append(deadlines[0])
    return comparison


def question_payload(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "parent_id": row["parent_id"], "status": row["status"], "question": row["question"],
        "turns": json.loads(row["turns"]), "context": json.loads(row["context"]), "as_of": row["as_of"],
        "created_at": row["created_at"], "result": None,
    }


def encode_question(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    payload = question_payload(row)
    if not row["result"]:
        return payload
    result = json.loads(row["result"])
    if row["status"] == "completed":
        versions = visible_versions(conn, row["role"])
        revision = corpus_revision(conn)
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
        result["department_comparison"] = (
            department_comparison(result["departments"], all_citations) if result["outcome"] == "answered" else []
        )
        result["practices"] = published_practices(conn, citations, row["role"])
        result["quality_answer"] = None
        quality_id = result.pop("quality_answer_id", None)
        if quality_id and result["outcome"] != "conflict":
            source_outcome = "not_found" if result["outcome"] == "quality_answer" else result["outcome"]
            quality = find_quality_answer(conn, row, source_outcome, revision, versions, case_id=quality_id)
            if quality is None:
                fail(409, "source_access_changed")
            result["quality_answer"] = {key: quality[key] for key in ("id", "question", "answer", "reviewed_at")}
        if case_id := result.pop("quality_case_id"):
            case = conn.execute("SELECT id, status, question_id FROM quality_cases WHERE id = ?", (case_id,)).fetchone()
            result["quality_case"] = {"id": case["id"], "status": case["status"], "mail": mail_thread(conn, case_id)}
            blocked = publication_block(result["outcome"], result["corpus_revision"], revision)
            if (case["status"] == "published" and case["question_id"] == row["id"]
                    and result["quality_answer"] is None and blocked is None):
                quality = conn.execute(
                    "SELECT id, canonical_question AS question, answer, reviewed_at FROM quality_cases WHERE id = ?",
                    (case_id,),
                ).fetchone()
                if result["outcome"] != "partial" or not changes_known_deadline(
                    conn, row["id"], date.fromisoformat(row["as_of"]), versions, quality["answer"],
                ):
                    result["quality_answer"] = dict(quality)
        else:
            result["quality_case"] = None
        result["corpus_changed"] = result["corpus_revision"] != revision
    payload["result"] = result
    return payload


def chat_messages(conn: sqlite3.Connection, chat_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT latest.* FROM chat_roots r JOIN questions root ON root.id = r.root_id
        JOIN questions latest ON latest.id = (
            SELECT q.id FROM questions q WHERE q.root_id = root.id ORDER BY q.created_at DESC, q.rowid DESC LIMIT 1
        ) WHERE r.chat_id = ? ORDER BY root.rowid""",
        (chat_id,),
    )
    messages = []
    for row in rows:
        try:
            messages.append(encode_question(conn, row))
        except HTTPException as error:
            if error.detail != "source_access_changed":
                raise
            messages.append({
                **question_payload(row), "status": "failed", "result": {"error_code": error.detail, "latency_ms": 0},
            })
    return messages
