"""Pixiv FANBOX public post list.

Uses the public JSON API with the Origin header their web app sends. Restricted
posts contribute a title and a link only. A cover URL is remembered when the
API includes one; the image is not requested during the poll.
"""

from __future__ import annotations

from urllib.parse import urlencode

from herald.config import Settings
from herald.errors import HeraldError, ParseFailure
from herald.http import HttpClient
from herald.models import FetchResult, FoundItem, Subscription
from herald.routing import require_segment
from herald.util import clip, is_clean_https, usable_cover_url

FANBOX_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://www.fanbox.cc",
    "Referer": "https://www.fanbox.cc/",
}


class FanboxProvider:
    name = "fanbox"

    def fetch(self, sub: Subscription, client: HttpClient, settings: Settings) -> FetchResult:
        creator = require_segment(sub.target, "FANBOX creator")
        query = urlencode({"creatorId": creator, "limit": "20"})
        url = f"https://api.fanbox.cc/post.listCreator?{query}"
        payload, final = client.get_json(url, headers=FANBOX_HEADERS)
        if _host(final) != "api.fanbox.cc":
            raise HeraldError("FANBOX redirected to an unexpected host")
        items, display = _parse(payload, creator)
        return FetchResult(
            items=items,
            display_name=display,
            canonical_url=f"https://www.fanbox.cc/@{creator}",
        )


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url).hostname or "").lower()


def _parse(payload: object, creator: str) -> tuple[list[FoundItem], str | None]:
    if not isinstance(payload, dict):
        raise ParseFailure("FANBOX response was not an object")
    body = payload.get("body")
    if not isinstance(body, dict):
        error = payload.get("error")
        if error:
            raise HeraldError(f"FANBOX refused the request ({clip(str(error), 120)})")
        raise ParseFailure("FANBOX response has no post list")
    raw_items = body.get("items")
    if not isinstance(raw_items, list):
        raise ParseFailure("FANBOX response has no post list")
    display = _creator_name(body)
    items: list[FoundItem] = []
    seen: set[str] = set()
    for row in raw_items:
        if not isinstance(row, dict) or len(items) >= 20:
            continue
        post_id = str(row.get("id") or "").strip()
        if not post_id.isdigit() or post_id in seen:
            continue
        title = clip(str(row.get("title") or "")) or "(untitled)"
        if row.get("isRestricted") is True:
            title = f"{title} (supporters only)"
        published = row.get("publishedDatetime") or row.get("updatedDatetime")
        url = f"https://www.fanbox.cc/@{creator}/posts/{post_id}"
        if not is_clean_https(url):
            continue
        seen.add(post_id)
        items.append(
            FoundItem(
                key=post_id,
                title=title,
                url=url,
                published_at=str(published) if published else None,
                cover_url=usable_cover_url(_text(row.get("coverImageUrl"))),
            )
        )
    return items, display


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _creator_name(body: dict) -> str | None:
    creator = body.get("creator")
    if isinstance(creator, dict):
        user = creator.get("user")
        if isinstance(user, dict) and user.get("name"):
            return clip(str(user["name"]), 120)
    return None
