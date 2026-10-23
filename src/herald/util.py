from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def collapse(text: str) -> str:
    return " ".join(text.split())


def clip(text: str, limit: int = 300) -> str:
    text = collapse(text)
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def same_site(expected: str, actual: str) -> bool:
    def norm(host: str) -> str:
        return host.lower().removeprefix("www.")

    return bool(expected) and norm(expected) == norm(actual)


def hostname_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_clean_https(url: str) -> bool:
    if any(char in url for char in "\r\n\t "):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(parts.hostname) and parts.port is None


def usable_cover_url(url: str | None) -> str | None:
    if not url or not is_clean_https(url):
        return None
    parts = urlsplit(url)
    if parts.username or parts.password:
        return None
    return url
