from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import defaultdict, deque
from dataclasses import dataclass
from hmac import compare_digest, digest
import json
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from time import monotonic, time
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import Response

from corp_docs_agent.core.settings import Settings


COOKIE = "corp_docs_session"
SESSION_SECONDS = 86_400 * 10


@dataclass(frozen=True)
class Principal:
    id: str
    role: str


def local_secret(storage: Path) -> str:
    storage.mkdir(parents=True, exist_ok=True)
    path = storage / "session.key"
    try:
        with path.open("x", encoding="ascii") as stream:
            stream.write(token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text(encoding="ascii")


class Sessions:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.secret = (settings.cookie_secret or local_secret(settings.storage)).encode()

    def read(self, request: Request) -> Principal | None:
        cookie = request.cookies.get(COOKIE, "")
        if len(cookie) > 512:
            return None
        try:
            body, signature = cookie.split(".", 1)
            expected = digest(self.secret, body.encode("ascii"), "sha256").hex()
            if not compare_digest(signature, expected):
                return None
            principal, role, expires = json.loads(urlsafe_b64decode(body))
            if role not in {"demo", "staff", "quality"} or expires < time():
                return None
            if (role == "staff" and not self.settings.staff_code) or (role == "quality" and not self.settings.quality_code):
                return None
            return Principal(principal, role)
        except (TypeError, ValueError, UnicodeError):
            return None

    def issue(self, response: Response, role: str, previous: Principal | None = None) -> Principal:
        principal = Principal(previous.id if previous else uuid4().hex, role)
        body = urlsafe_b64encode(json.dumps([principal.id, role, int(time()) + SESSION_SECONDS]).encode())
        signature = digest(self.secret, body, "sha256").hex()
        response.set_cookie(
            COOKIE, body.decode("ascii") + "." + signature, max_age=SESSION_SECONDS,
            secure=self.settings.secure_cookie, httponly=True, samesite="strict", path="/",
        )
        return principal


class LoginAttempts:
    def __init__(self) -> None:
        self.attempts: dict[str, deque] = defaultdict(deque)
        self.lock = Lock()

    def admit(self, key: str) -> bool:
        with self.lock:
            threshold = monotonic() - 60
            if len(self.attempts) >= 1000:
                self.attempts = defaultdict(deque, {
                    ip: times for ip, times in self.attempts.items() if times[-1] >= threshold
                })
                if len(self.attempts) >= 1000 and key not in self.attempts:
                    return False
            values = self.attempts[key]
            while values and values[0] < threshold:
                values.popleft()
            if len(values) >= 10:
                return False
            values.append(monotonic())
            return True
