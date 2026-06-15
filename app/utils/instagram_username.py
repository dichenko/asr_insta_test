import re
from urllib.parse import urlparse


_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_SERVICE_PATHS = {"p", "reel", "reels", "stories", "explore", "accounts", "direct"}


def normalize_instagram_username(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None

    if value.startswith("@"):
        value = value[1:].strip()
        return _normalize_plain_username(value)

    parsed_value = value
    if parsed_value.startswith(("instagram.com/", "www.instagram.com/")):
        parsed_value = f"https://{parsed_value}"

    parsed = urlparse(parsed_value)
    if parsed.scheme or parsed.netloc:
        host = parsed.netloc.lower()
        if host not in {"instagram.com", "www.instagram.com"}:
            return None
        path_parts = [part for part in parsed.path.split("/") if part]
        if not path_parts:
            return None
        username = path_parts[0]
        return _normalize_plain_username(username)

    return _normalize_plain_username(value)


def _normalize_plain_username(value: str) -> str | None:
    if not value or value.lower() in _SERVICE_PATHS:
        return None
    if not _USERNAME_RE.fullmatch(value):
        return None
    return value.lower()
