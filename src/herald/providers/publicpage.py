"""Best-effort public HTML for Patreon and SubscribeStar.

A page that is only a login wall, age gate, or bot challenge fails loudly.
Herald does not launch a browser and does not solve a challenge. An empty
public shell is also an error, so a client-rendered page is not recorded as
"this creator has no posts".
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from herald.config import Settings
from herald.errors import AuthRequired, ChallengeDetected, HeraldError
from herald.http import HttpClient
from herald.models import FetchResult, FoundItem, Subscription
from herald.pageguard import looks_like_age_gate, looks_like_challenge, looks_like_login
from herald.routing import require_segment
from herald.util import clip, collapse, hostname_of, is_clean_https, same_site, usable_cover_url

POST_ID = re.compile(r"/posts/(?:[A-Za-z0-9_-]*-)?(\d{3,})")
MAX_POSTS = 40


class PublicPageProvider:
    def __init__(self, name: str) -> None:
        self.name = name

    def fetch(self, sub: Subscription, client: HttpClient, settings: Settings) -> FetchResult:
        page_url = canonical_page(self.name, sub.target)
        html, final = client.get_text(page_url)
        final_host = hostname_of(final)
        if not _allowed_final_host(self.name, sub.target, final_host):
            raise HeraldError(f"{self.name} redirected to unexpected host {final_host}")
        if looks_like_challenge(html, final):
            message = f"{self.name} returned a bot challenge. Herald does not solve challenges."
            client.pause_host(final, message)
            raise ChallengeDetected(message)
        items, display = _posts(html, final, self.name)
        if not items and (looks_like_login(html, final) or looks_like_age_gate(html)):
            raise AuthRequired(
                f"{self.name} public page requires a login or age gate. "
                "Herald will not bypass it. Pause this subscription if the page never stays public."
            )
        if not items:
            raise AuthRequired(
                f"{self.name} public HTML contained no posts. The page is probably "
                "behind a login wall or rendered only in a browser. Herald will not "
                "launch a browser or solve a challenge."
            )
        return FetchResult(items=items, display_name=display, canonical_url=page_url)


def canonical_page(provider: str, target: str) -> str:
    if provider == "patreon":
        if target.startswith("cw:"):
            vanity = require_segment(target[3:], "Patreon creator")
            return f"https://www.patreon.com/cw/{vanity}"
        vanity = require_segment(target, "Patreon creator")
        return f"https://www.patreon.com/{vanity}"
    if provider == "subscribestar":
        site, _, name = target.partition(":")
        creator = require_segment(name, "SubscribeStar creator")
        host = "subscribestar.adult" if site == "adult" else "www.subscribestar.com"
        return f"https://{host}/{creator}"
    raise HeraldError(f"no public page for {provider}")


def _allowed_final_host(provider: str, target: str, host: str) -> bool:
    if provider == "patreon":
        return same_site("patreon.com", host)
    if provider == "subscribestar":
        expected = "subscribestar.adult" if target.startswith("adult:") else "subscribestar.com"
        return same_site(expected, host)
    return False


def _posts(html: str, page_url: str, provider: str) -> tuple[list[FoundItem], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[FoundItem] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        match = POST_ID.search(anchor["href"])
        if not match:
            continue
        post_id = match.group(1)
        if post_id in seen or len(items) >= MAX_POSTS:
            continue
        title = clip(anchor.get_text(" ", strip=True)) or f"Post {post_id}"
        url = _clean_post_url(provider, page_url, post_id)
        if not is_clean_https(url):
            continue
        seen.add(post_id)
        items.append(
            FoundItem(key=post_id, title=title, url=url, cover_url=_cover_from_anchor(anchor, page_url))
        )
    return items, _display_name(soup, provider)


def _cover_from_anchor(anchor, page_url: str) -> str | None:
    image = anchor.find("img")
    if image is None:
        return None
    raw = image.get("data-src") or image.get("src") or ""
    if not raw or raw.startswith("data:"):
        return None
    return usable_cover_url(urljoin(page_url, raw))


def _clean_post_url(provider: str, page_url: str, post_id: str) -> str:
    if provider == "patreon":
        return f"https://www.patreon.com/posts/{post_id}"
    host = hostname_of(page_url)
    return f"https://{host}/posts/{post_id}"


def _display_name(soup: BeautifulSoup, provider: str) -> str | None:
    if soup.title is None:
        return None
    text = collapse(soup.title.get_text())
    head = re.split(r"[|\-–—]", text)[0].strip()
    if not head or head.lower() in {provider, "home", "patreon", "subscribestar"}:
        return None
    return clip(head, 120)
