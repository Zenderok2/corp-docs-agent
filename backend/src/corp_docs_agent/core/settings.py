from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    storage: Path
    database: Path
    chat_url: str = "http://127.0.0.1:8081/v1"
    chat_model: str = "corp-chat"
    answer_timeout: float = 90.0
    max_pending: int = 8
    max_pending_per_user: int = 2
    max_upload_mb: int = 25
    staff_code: str = ""
    quality_code: str = ""
    cookie_secret: str = ""
    public_origin: str = ""
    environment: str = "local"
    retrieval_mode: str = "lexical"
    embedding_url: str = "http://127.0.0.1:8082/v1"
    embedding_model: str = "corp-embed"
    embedding_dimensions: int = 1024
    embedding_profile: str = "qwen3-embedding-0.6b-q8-v1"
    chroma_url: str = "http://127.0.0.1:8001"
    build_sha: str = "local"

    @property
    def secure_cookie(self) -> bool:
        return self.public_origin.startswith("https://")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        storage = Path(os.getenv("APP_STORAGE_ROOT", "runtime")).resolve()
        settings = cls(
            storage=storage,
            database=Path(os.getenv("DATABASE_PATH", str(storage / "app.sqlite3"))).resolve(),
            chat_url=os.getenv("LLM_BASE_URL", cls.chat_url).rstrip("/"),
            chat_model=os.getenv("LLM_MODEL", cls.chat_model),
            answer_timeout=float(os.getenv("ANSWER_TIMEOUT_SECONDS", str(cls.answer_timeout))),
            max_pending=int(os.getenv("MAX_PENDING_QUESTIONS", str(cls.max_pending))),
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", str(cls.max_upload_mb))),
            staff_code=os.getenv("STAFF_ACCESS_CODE", ""),
            quality_code=os.getenv("QUALITY_ACCESS_CODE", ""),
            cookie_secret=os.getenv("SESSION_SECRET", ""),
            public_origin=os.getenv("PUBLIC_ORIGIN", "").rstrip("/"),
            environment=os.getenv("APP_ENV", cls.environment),
            retrieval_mode=os.getenv("RETRIEVAL_MODE", cls.retrieval_mode),
            embedding_url=os.getenv("EMBEDDING_BASE_URL", cls.embedding_url).rstrip("/"),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            embedding_dimensions=int(os.getenv("EMBEDDING_DIMENSIONS", "1024")),
            embedding_profile=os.getenv("EMBEDDING_PROFILE_VERSION", cls.embedding_profile),
            chroma_url=os.getenv("CHROMA_BASE_URL", cls.chroma_url).rstrip("/"),
            build_sha=os.getenv("BUILD_SHA", cls.build_sha),
        )
        if settings.retrieval_mode not in {"lexical", "hybrid"}:
            raise ValueError("RETRIEVAL_MODE must be lexical or hybrid")
        if not (5 <= settings.answer_timeout <= 180 and 1 <= settings.max_pending <= 32):
            raise ValueError("Invalid answer timeout or queue limit")
        if settings.staff_code and settings.staff_code == settings.quality_code:
            raise ValueError("The staff and quality access codes must differ")
        if settings.environment != "local" and (
            not settings.secure_cookie
            or len(settings.cookie_secret) < 32
            or len(settings.quality_code) < 16
        ):
            raise ValueError("Hosted mode requires HTTPS and separate strong secrets")
        return settings
