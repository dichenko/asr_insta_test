import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from app.config import get_settings

OAUTH_SESSION_COOKIE = "ig_auth_session"


def _cookie_secret() -> str:
    settings = get_settings()
    secret = settings.app_secret_key or settings.token_encryption_key
    if not secret:
        raise RuntimeError("APP_SECRET_KEY or TOKEN_ENCRYPTION_KEY is required for OAuth session cookies")
    return secret


def sign_auth_session_cookie(auth_session_id: uuid.UUID, expires_at: datetime) -> str:
    expires_ts = int(expires_at.timestamp())
    body = f"{auth_session_id}.{expires_ts}"
    signature = hmac.new(_cookie_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_auth_session_cookie(cookie_value: str | None) -> uuid.UUID | None:
    if not cookie_value:
        return None

    parts = cookie_value.split(".")
    if len(parts) != 3:
        return None

    session_id, expires_raw, signature = parts
    body = f"{session_id}.{expires_raw}"
    expected = hmac.new(_cookie_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        expires_ts = int(expires_raw)
    except ValueError:
        return None

    if expires_ts <= int(datetime.now(timezone.utc).timestamp()):
        return None

    try:
        return uuid.UUID(session_id)
    except ValueError:
        return None
