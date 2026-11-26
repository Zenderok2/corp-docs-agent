from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    sha256 TEXT NOT NULL,
    file_suffix TEXT NOT NULL,
    metadata TEXT NOT NULL CHECK (json_valid(metadata)),
    warnings TEXT NOT NULL CHECK (json_valid(warnings)),
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES document_versions(id),
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    locator TEXT NOT NULL CHECK (json_valid(locator)),
    UNIQUE (version_id, ordinal)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    block_id TEXT NOT NULL REFERENCES blocks(id),
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset)
);
CREATE INDEX IF NOT EXISTS chunk_block ON chunks(block_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED, body, heading, title, tokenize = 'unicode61 remove_diacritics 0'
);
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('demo', 'staff', 'quality')),
    parent_id TEXT REFERENCES questions(id),
    root_id TEXT NOT NULL REFERENCES questions(id),
    idempotency_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    question TEXT NOT NULL,
    turns TEXT NOT NULL CHECK (json_valid(turns)),
    context TEXT NOT NULL CHECK (json_valid(context)),
    document_ids TEXT NOT NULL CHECK (json_valid(document_ids)),
    as_of TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    result TEXT CHECK (result IS NULL OR json_valid(result)),
    created_at TEXT NOT NULL,
    UNIQUE (principal, idempotency_key)
);
CREATE INDEX IF NOT EXISTS question_queue ON questions(status, created_at);
CREATE INDEX IF NOT EXISTS question_history ON questions(principal, created_at);
CREATE INDEX IF NOT EXISTS question_thread ON questions(root_id, created_at);
CREATE TABLE IF NOT EXISTS citations (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id),
    chunk_id TEXT NOT NULL REFERENCES chunks(id),
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS citation_question ON citations(question_id);
CREATE TABLE IF NOT EXISTS quality_cases (
    id TEXT PRIMARY KEY,
    root_id TEXT NOT NULL UNIQUE REFERENCES questions(id),
    question_id TEXT NOT NULL REFERENCES questions(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'published', 'dismissed', 'withdrawn')),
    canonical_question TEXT,
    answer TEXT,
    effective_to TEXT,
    reviewed_by TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS quality_keys (
    case_id TEXT NOT NULL REFERENCES quality_cases(id),
    question_key TEXT NOT NULL,
    PRIMARY KEY (question_key, case_id)
);
CREATE TABLE IF NOT EXISTS practices (
    id TEXT PRIMARY KEY,
    citation_id TEXT NOT NULL REFERENCES citations(id),
    version_id TEXT NOT NULL REFERENCES document_versions(id),
    author TEXT NOT NULL,
    situation TEXT NOT NULL,
    action_taken TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_review', 'approved', 'dismissed')),
    reviewed_by TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    UNIQUE (author, citation_id)
);
CREATE INDEX IF NOT EXISTS practice_version ON practices(version_id, status);
CREATE TABLE IF NOT EXISTS index_generations (
    id TEXT PRIMARY KEY,
    corpus_revision TEXT NOT NULL,
    profile TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    collection_id TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO app_meta VALUES ('corpus_revision', '0');
PRAGMA user_version = 1;
"""


def json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class CorpusVersion:
    id: str
    document_id: str
    sha256: str
    file_suffix: str
    metadata: dict
    warnings: list

    def applies_on(self, as_of: date) -> bool:
        day = as_of.isoformat()
        start = self.metadata["effective_from"]
        end = self.metadata["effective_to"]
        return (start is None or start <= day) and (end is None or end >= day)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "version_label": self.metadata["version"],
            "title": self.metadata["title"],
            "kind": self.metadata["kind"],
            "publication_status": self.metadata["status"],
            "effective_from": self.metadata["effective_from"],
            "effective_to": self.metadata["effective_to"],
            "scope": self.metadata["scope"],
        }


def role_groups(role: str, *, for_review: bool = False) -> tuple[str, ...]:
    if role == "quality":
        return ("demo", "staff", "quality") if for_review else ("staff", "quality")
    if role == "staff":
        return ("staff",)
    return ("demo",)


def visible_versions(conn: sqlite3.Connection, role: str, *, for_review: bool = False) -> dict[str, CorpusVersion]:
    groups = role_groups(role, for_review=for_review)
    rows = conn.execute(
        """
        SELECT * FROM document_versions AS v
        WHERE json_extract(v.metadata, '$.status') != 'withdrawn'
          AND EXISTS (
              SELECT 1 FROM json_each(v.metadata, '$.groups')
              WHERE value IN (SELECT value FROM json_each(?))
          )
        ORDER BY json_extract(v.metadata, '$.title'), v.id
        """,
        (json_dump(groups),),
    )
    return {
        row["id"]: CorpusVersion(
            id=row["id"],
            document_id=row["document_id"],
            sha256=row["sha256"],
            file_suffix=row["file_suffix"],
            metadata=json.loads(row["metadata"]),
            warnings=json.loads(row["warnings"]),
        )
        for row in rows
    }


def corpus_revision(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT value FROM app_meta WHERE key = 'corpus_revision'").fetchone()[0]


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
            elif version != 1:
                raise RuntimeError("Unsupported database schema")
        finally:
            conn.close()

    def recover_jobs(self) -> None:
        with self.connect(write=True) as conn:
            conn.execute("UPDATE questions SET status = 'queued' WHERE status = 'running'")

    def claim_question(self) -> sqlite3.Row | None:
        with self.connect(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM questions WHERE status = 'queued' ORDER BY created_at, rowid LIMIT 1"
            ).fetchone()
            if row:
                conn.execute("UPDATE questions SET status = 'running' WHERE id = ?", (row["id"],))
            return row
