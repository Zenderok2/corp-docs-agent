from contextlib import asynccontextmanager
from datetime import date, datetime
from hmac import compare_digest
import json
from threading import Thread
from typing import Annotated
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from corp_docs_agent.agent.answer import AnswerWorker, MAX_FOLLOW_UPS
from corp_docs_agent.api.auth import COOKIE, LoginAttempts, Principal, Sessions
from corp_docs_agent.api.records import (
    EVIDENCE_SQL, encode_citation, encode_question, enqueue, fail, owned_question,
)
from corp_docs_agent.core.settings import Settings
from corp_docs_agent.domain.models import Ask, CaseReview, FollowUp, Login, PracticeReport, PracticeReview
from corp_docs_agent.persistence.store import Store, json_dump, now, visible_versions
from corp_docs_agent.providers.llama import Chat
from corp_docs_agent.retrieval.terms import canonical_question


Idempotency = Annotated[str | None, Header(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")]
MOSCOW = ZoneInfo("Europe/Moscow")


def create_app(settings: Settings | None = None, chat: Chat | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.database)
    sessions = Sessions(settings)
    login_attempts = LoginAttempts()
    engine = chat or Chat(settings)
    vectors = None
    if settings.retrieval_mode == "hybrid":
        from corp_docs_agent.providers.vectors import VectorIndex

        vectors = VectorIndex(settings)
    worker = AnswerWorker(store, engine, vectors)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.initialize()
        store.recover_jobs()
        worker.stopped.clear()
        thread = Thread(target=worker.run, daemon=True, name="answer-queue")
        thread.start()
        _app.state.answer_thread = thread
        yield
        worker.stopped.set()
        worker.wakeup.set()
        thread.join(timeout=5)

    app = FastAPI(
        title="Corporate Documents Assistant", version="0.1.0", lifespan=lifespan,
        docs_url="/api/docs" if settings.environment == "local" else None,
        openapi_url="/api/openapi.json" if settings.environment == "local" else None,
        redoc_url=None,
    )

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            trusted = {settings.public_origin} if settings.public_origin else {
                str(request.base_url).rstrip("/"), "http://localhost:5173", "http://127.0.0.1:5173",
            }
            if origin and origin not in trusted:
                return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or len(length) > 8 or int(length) > 32_768):
                return JSONResponse({"detail": "request_too_large"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_request: Request, _error: RequestValidationError):
        return JSONResponse({"detail": "invalid_request"}, status_code=422)

    def principal(request: Request) -> Principal:
        user = sessions.read(request)
        if user is None:
            fail(401, "session_required")
        return user

    def reviewer(user: Principal = Depends(principal)) -> Principal:
        if user.role != "quality":
            fail(403, "quality_access_required")
        return user

    def session_state(user: Principal) -> dict:
        with store.connect() as conn:
            versions = visible_versions(conn, user.role)
        return {
            "role": user.role, "staff_login_available": bool(settings.staff_code),
            "quality_login_available": bool(settings.quality_code), "visible_versions": len(versions),
            "build_sha": settings.build_sha,
        }

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "ok", "build_sha": settings.build_sha}

    @app.get("/health/ready")
    def ready() -> Response:
        with store.connect() as conn:
            corpus = bool(visible_versions(conn, "quality", for_review=True))
        checks = {
            "corpus": corpus, "chat": engine.ready(), "worker": app.state.answer_thread.is_alive(),
            "index": not vectors or vectors.ready(store),
        }
        ok = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ok else "unready", "checks": checks, "build_sha": settings.build_sha},
            status_code=200 if ok else 503,
        )

    @app.get("/api/v1/session")
    def get_session(request: Request, response: Response) -> dict:
        user = sessions.read(request) or sessions.issue(response, "demo")
        return session_state(user)

    @app.post("/api/v1/session")
    def sign_in(data: Login, request: Request, response: Response) -> dict:
        key = request.client.host if request.client else "unknown"
        if not login_attempts.admit(key):
            fail(429, "login_throttled")
        expected = settings.quality_code if data.role == "quality" else settings.staff_code
        if not expected or not compare_digest(data.code.encode(), expected.encode()):
            fail(401, "invalid_access_code")
        user = sessions.issue(response, data.role, sessions.read(request))
        return session_state(user)

    @app.delete("/api/v1/session")
    def sign_out(request: Request, response: Response) -> dict:
        previous = sessions.read(request)
        if not previous:
            response.delete_cookie(COOKIE)
        return session_state(sessions.issue(response, "demo", previous))

    @app.get("/api/v1/documents")
    def documents(
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
        after: Annotated[str, Query(max_length=180)] = "",
        user: Principal = Depends(principal),
    ) -> dict:
        with store.connect() as conn:
            versions = visible_versions(conn, user.role)
            ids = sorted(key for key in versions if key > after)[:limit + 1]
            return {
                "items": [versions[key].summary() for key in ids[:limit]],
                "next_cursor": ids[limit - 1] if len(ids) > limit else None,
            }

    @app.get("/api/v1/document-versions/{version_id}/content")
    def document_content(
        version_id: str, after: Annotated[int, Query(ge=0)] = 0,
        user: Principal = Depends(principal),
    ) -> dict:
        with store.connect() as conn:
            versions = visible_versions(conn, user.role)
            if version_id not in versions:
                fail(404, "document_not_found")
            rows = conn.execute(
                "SELECT id, ordinal, text, locator FROM blocks WHERE version_id = ? AND ordinal > ? ORDER BY ordinal LIMIT 16",
                (version_id, after),
            ).fetchall()
            return {
                "version": versions[version_id].summary(),
                "items": [{
                    "id": row["id"], "ordinal": row["ordinal"], "text": row["text"],
                    "locator": json.loads(row["locator"]),
                } for row in rows[:15]],
                "next_cursor": rows[14]["ordinal"] if len(rows) > 15 else None,
            }

    @app.post("/api/v1/questions", status_code=202)
    def ask(data: Ask, idempotency_key: Idempotency = None, user: Principal = Depends(principal)) -> dict:
        with store.connect(write=True) as conn:
            result = enqueue(
                conn, settings, user, question=data.question, turns=[], context=data.context.stored(),
                as_of=data.as_of or datetime.now(MOSCOW).date(), document_ids=sorted(set(data.document_ids)),
                parent=None, idempotency_key=idempotency_key, request_body=data.model_dump_json(),
            )
        worker.wakeup.set()
        return result

    @app.get("/api/v1/questions")
    def history(
        limit: Annotated[int, Query(ge=1, le=50)] = 15, user: Principal = Depends(principal),
    ) -> dict:
        with store.connect() as conn:
            rows = conn.execute(
                """SELECT q.id, q.parent_id, q.question, q.status, q.created_at FROM questions q
                WHERE q.principal = ? AND q.role = ? AND q.id = (
                  SELECT f.id FROM questions f WHERE f.root_id = q.root_id AND f.principal = q.principal
                  ORDER BY f.created_at DESC, f.rowid DESC LIMIT 1
                ) ORDER BY q.created_at DESC, q.rowid DESC LIMIT ?""",
                (user.id, user.role, limit),
            )
            return {"items": [dict(row) for row in rows]}

    @app.get("/api/v1/questions/{question_id}")
    def get_question(question_id: str, user: Principal = Depends(principal)) -> dict:
        with store.connect() as conn:
            return encode_question(conn, owned_question(conn, question_id, user))

    @app.post("/api/v1/questions/{question_id}/follow-ups", status_code=202)
    def follow_up(
        question_id: str, data: FollowUp, idempotency_key: Idempotency = None, user: Principal = Depends(principal),
    ) -> dict:
        with store.connect(write=True) as conn:
            parent = owned_question(conn, question_id, user)
            if parent["status"] != "completed":
                fail(409, "question_in_progress")
            prior = encode_question(conn, parent)["result"]
            turns = json.loads(parent["turns"])
            if prior["outcome"] != "needs_clarification" or len(turns) >= MAX_FOLLOW_UPS:
                fail(409, "no_open_clarification")
            context = {**json.loads(parent["context"]), **data.context.model_dump(exclude_none=True)}
            if data.context.department:
                context.pop("departments", None)
            if data.context.departments:
                context["department"] = None
            result = enqueue(
                conn, settings, user, question=parent["question"], turns=[*turns, data.answer],
                context=context, as_of=date.fromisoformat(parent["as_of"]), document_ids=json.loads(parent["document_ids"]),
                parent=parent, idempotency_key=idempotency_key, request_body=data.model_dump_json(),
            )
        worker.wakeup.set()
        return result

    @app.get("/api/v1/citations/{citation_id}")
    def citation_detail(citation_id: str, user: Principal = Depends(principal)) -> dict:
        with store.connect() as conn:
            row = conn.execute(
                EVIDENCE_SQL + """ JOIN questions q ON q.id = s.question_id
                WHERE s.id = ? AND ((q.principal = ? AND q.role = ?) OR ? = 'quality')""",
                (citation_id, user.id, user.role, user.role),
            ).fetchone()
            versions = visible_versions(conn, user.role, for_review=user.role == "quality")
            if row is None or row["version_id"] not in versions:
                fail(404, "citation_not_found")
            return encode_citation(row, versions, context=True)

    @app.post("/api/v1/questions/{question_id}/practice-reports", status_code=201)
    def submit_practice(question_id: str, data: PracticeReport, user: Principal = Depends(principal)) -> dict:
        with store.connect(write=True) as conn:
            row = owned_question(conn, question_id, user)
            encoded = encode_question(conn, row)
            result = encoded["result"] if row["status"] == "completed" else {}
            citation = next((item for item in result.get("citations", []) if item["id"] == data.citation_id), None)
            if citation is None:
                fail(404, "citation_not_found")
            existing = conn.execute(
                "SELECT id, status, situation, action_taken FROM practices WHERE author = ? AND citation_id = ?",
                (user.id, data.citation_id),
            ).fetchone()
            if existing:
                if existing["situation"] != data.situation or existing["action_taken"] != data.action_taken:
                    fail(409, "practice_already_submitted")
                return {"id": existing["id"], "status": existing["status"]}
            report_id = uuid4().hex
            conn.execute(
                """INSERT INTO practices(id, citation_id, version_id, author, situation, action_taken, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending_review', ?)""",
                (report_id, data.citation_id, citation["document_version_id"], user.id, data.situation, data.action_taken, now()),
            )
            return {"id": report_id, "status": "pending_review"}

    @app.get("/api/v1/quality/cases")
    def quality_cases(_user: Principal = Depends(reviewer)) -> dict:
        with store.connect() as conn:
            rows = conn.execute(
                """SELECT k.id, k.status, k.question_id, k.canonical_question, k.answer, k.effective_to,
                k.created_at, k.reviewed_at, q.question, q.turns, q.context, q.as_of, q.role,
                json_extract(q.result, '$.message_code') AS reason
                FROM quality_cases k JOIN questions q ON q.id = k.question_id
                ORDER BY (k.status = 'pending') DESC, k.created_at DESC LIMIT 75"""
            )
            versions = visible_versions(conn, "quality", for_review=True)
            items = []
            for row in rows:
                item = dict(row)
                item["turns"] = json.loads(item["turns"])
                item["context"] = json.loads(item["context"])
                item["citations"] = [
                    encode_citation(citation, versions)
                    for citation in conn.execute(EVIDENCE_SQL + " WHERE s.question_id = ?", (row["question_id"],))
                    if citation["version_id"] in versions
                ]
                items.append(item)
            return {"items": items}

    @app.put("/api/v1/quality/cases/{case_id}")
    def review_case(case_id: str, data: CaseReview, user: Principal = Depends(reviewer)) -> dict:
        with store.connect(write=True) as conn:
            row = conn.execute(
                """SELECT k.status, q.as_of, q.role, q.context, q.document_ids, q.turns
                FROM quality_cases k JOIN questions q ON q.id = k.question_id WHERE k.id = ?""",
                (case_id,),
            ).fetchone()
            if row is None:
                fail(404, "case_not_found")
            if data.decision == "withdraw":
                if row["status"] != "published":
                    fail(409, "case_not_published")
                conn.execute(
                    "UPDATE quality_cases SET status = 'withdrawn', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                    (user.id, now(), case_id),
                )
                return {"id": case_id, "status": "withdrawn"}
            if row["status"] != "pending":
                fail(409, "case_already_reviewed")
            if data.decision == "dismiss":
                conn.execute(
                    "UPDATE quality_cases SET status = 'dismissed', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                    (user.id, now(), case_id),
                )
                return {"id": case_id, "status": "dismissed"}
            effective_to = data.effective_to.isoformat() if data.effective_to else None
            if effective_to and effective_to < row["as_of"]:
                fail(422, "invalid_effective_date")
            keys = sorted({canonical_question(text) for text in [data.question, *data.aliases]})
            if "" in keys:
                fail(422, "invalid_publication_question")
            has_conflict = conn.execute(
                """SELECT 1 FROM quality_cases k JOIN quality_keys keys ON k.id = keys.case_id
                JOIN questions q ON q.id = k.question_id
                WHERE k.status = 'published' AND k.answer != ?
                  AND keys.question_key IN (SELECT value FROM json_each(?))
                  AND q.role = ? AND q.context = ? AND q.document_ids = ? AND q.turns = ?
                  AND (k.effective_to IS NULL OR k.effective_to >= ?)
                  AND (? IS NULL OR q.as_of <= ?) LIMIT 1""",
                (data.answer, json_dump(keys), row["role"], row["context"], row["document_ids"], row["turns"],
                 row["as_of"], effective_to, effective_to),
            ).fetchone()
            if has_conflict:
                fail(409, "quality_answer_conflict")
            conn.execute(
                """UPDATE quality_cases SET status = 'published', canonical_question = ?, answer = ?,
                effective_to = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?""",
                (data.question, data.answer, effective_to, user.id, now(), case_id),
            )
            conn.executemany("INSERT INTO quality_keys VALUES (?, ?)", [(case_id, key) for key in keys])
            return {"id": case_id, "status": "published"}

    @app.get("/api/v1/quality/practices")
    def practice_queue(_user: Principal = Depends(reviewer)) -> dict:
        with store.connect() as conn:
            versions = visible_versions(conn, "quality", for_review=True)
            rows = conn.execute(
                """SELECT p.id, p.citation_id, p.version_id, p.situation, p.action_taken, p.status, p.created_at
                FROM practices p ORDER BY (p.status = 'pending_review') DESC, p.created_at DESC LIMIT 75"""
            )
            return {"items": [
                {**dict(row), "title": versions[row["version_id"]].metadata["title"] if row["version_id"] in versions else None}
                for row in rows
            ]}

    @app.put("/api/v1/quality/practices/{report_id}")
    def review_practice(report_id: str, data: PracticeReview, user: Principal = Depends(reviewer)) -> dict:
        with store.connect(write=True) as conn:
            row = conn.execute("SELECT status, version_id FROM practices WHERE id = ?", (report_id,)).fetchone()
            if row is None:
                fail(404, "practice_not_found")
            if row["status"] != "pending_review":
                fail(409, "practice_already_reviewed")
            if data.decision == "approve" and row["version_id"] not in visible_versions(conn, "quality", for_review=True):
                fail(409, "source_access_changed")
            status = "approved" if data.decision == "approve" else "dismissed"
            conn.execute(
                "UPDATE practices SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                (status, user.id, now(), report_id),
            )
            return {"id": report_id, "status": status}

    return app


app = create_app()
