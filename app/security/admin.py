import hashlib
import secrets
import time
from collections import OrderedDict

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_config

# Local-process protection. Production multi-worker deployments need a shared limiter.
_failures: OrderedDict[str, tuple[int, float]] = OrderedDict()


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
    return f"pbkdf2_sha256$600000${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(iterations) <= 2_000_000:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt.encode(),
            int(iterations),
        ).hex()
        return secrets.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def credential_stamp() -> str:
    config = get_config()
    if not config.admin_password_hash or not config.admin_session_secret:
        raise HTTPException(503, "Configura las credenciales administrativas")
    return hashlib.sha256(
        config.admin_password_hash.get_secret_value().encode()
    ).hexdigest()


def require_admin(request: Request) -> None:
    stamp = credential_stamp()
    if (
        request.session.get("admin") != get_config().admin_username
        or request.session.get("credential_stamp") != stamp
    ):
        raise HTTPException(303, headers={"Location": "/admin/login"})


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def verify_csrf(request: Request, value: str) -> None:
    token = request.session.get("csrf", "")
    if not token or not secrets.compare_digest(token, value):
        raise HTTPException(403, "Sesión inválida. Recarga la página.")


def check_login_limit(request: Request) -> str:
    ip = request.client.host if request.client else "unknown"
    count, since = _failures.get(ip, (0, time.monotonic()))
    if time.monotonic() - since > 900:
        _failures.pop(ip, None)
    elif count >= 5:
        raise HTTPException(429, "Demasiados intentos. Espera 15 minutos.")
    return ip


def failed_login(ip: str) -> None:
    count, since = _failures.get(ip, (0, time.monotonic()))
    _failures[ip] = (count + 1, since)
    _failures.move_to_end(ip)
    if len(_failures) > 10000:
        _failures.popitem(last=False)
