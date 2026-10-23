"""Kemono and Coomer public JSON feeds.

The Accept header matches the public API these archives document. Herald reads
post titles and ids. Attachment paths are ignored. A cover is kept only when
the post already has an https cover URL, and that image is not fetched here.
"""

from __future__ import annotations

import json

from herald.config import Settings
from herald.errors import ChallengeDetected, HeraldError, ParseFailure
from herald.http import HttpClient
from herald.models import FetchResult, FoundItem, Subscription
from herald.pageguard import looks_like_challenge
from herald.util import clip, is_clean_https, usable_cover_url

ARCHIVE_HEADERS = {"Accept": "text/css, application/json"}
MAX_POSTS = 50


class ArchiveProvider:
    def __init__(self, name: str, hosts: tuple[str, ...]) -> None:
        self.name = name
        self.hosts = hosts

    def fetch(self, sub: Subscription, client: HttpClient, settings: Settings) -> FetchResult:
        service, user, origin = _decode(sub, self.hosts)
        candidates = (
            f"{origin}/api/v1/{service}/user/{user}/posts",
            f"{origin}/api/v1/{service}/user/{user}",
        )
        saw_not_found = False
        for url in candidates:
            response = client.request(url, headers=ARCHIVE_HEADERS)
            if response.status_code == 404:
                saw_not_found = True
                continue
            if response.status_code != 200:
                raise HeraldError(f"{self.name} returned HTTP {response.status_code}")
            preview = response.content[:4000].decode("utf-8", "replace")
            if looks_like_challenge(preview):
                message = (
                    f"{self.name} returned a bot challenge. "
                    "Herald does not solve challenges and will not retry this host in this run."
                )
                client.pause_host(url, message)
                raise ChallengeDetected(message)
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise ParseFailure(f"{self.name} response was not JSON") from exc
            posts = _posts(payload)
            items = _items(posts, service, user, origin)
            return FetchResult(
                items=items,
                display_name=None,
                canonical_url=f"{origin}/{service}/user/{user}",
            )
        if saw_not_found:
            raise HeraldError(
                f"{self.name} returned HTTP 404 for {service}/{user}. "
                "The creator may not exist, or the public API path changed. "
                "Nothing was marked seen."
            )
        raise HeraldError(f"{self.name} did not return a post list")


def _decode(sub: Subscription, hosts: tuple[str, ...]) -> tuple[str, str, str]:
    try:
        ident, host = sub.target.rsplit("@", 1)
        service, user = ident.split(":", 1)
    except ValueError as exc:
        raise HeraldError(f"stored {sub.provider} target is invalid") from exc
    if host not in hosts or "/" in service or "/" in user:
        raise HeraldError(f"stored {sub.provider} target host is not allowed")
    return service, user, f"https://{host}"


def _cover(post: dict) -> str | None:
    for key in ("cover", "cover_url"):
        value = post.get(key)
        if isinstance(value, str):
            return usable_cover_url(value)
    return None


def _posts(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("posts", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    raise ParseFailure("archive response did not contain a post list")


def _items(posts: list[dict], service: str, user: str, origin: str) -> list[FoundItem]:
    items: list[FoundItem] = []
    seen: set[str] = set()
    for post in posts:
        if len(items) >= MAX_POSTS:
            break
        post_user = str(post.get("user") or user)
        post_service = str(post.get("service") or service).lower()
        if post_user != user or post_service != service:
            continue
        post_id = str(post.get("id") or "").strip()
        if not post_id or "/" in post_id or post_id in seen:
            continue
        title = clip(str(post.get("title") or "")) or "(untitled)"
        published = post.get("published") or post.get("added")
        published_at = str(published) if published else None
        url = f"{origin}/{service}/user/{user}/post/{post_id}"
        if not is_clean_https(url):
            continue
        seen.add(post_id)
        items.append(
            FoundItem(
                key=post_id,
                title=title,
                url=url,
                published_at=published_at,
                cover_url=_cover(post),
            )
        )
    return items
