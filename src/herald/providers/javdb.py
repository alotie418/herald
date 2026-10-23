"""JavDB actor search and actor-page parser.

The actor page is read without a query string. Work rows are anchors to ``/v/``
with a code in a nearby strong tag or in the link text. Herald stores the code,
the work link, and a cover URL when the card already has one. The image itself
is not saved, and is fetched only while sending a Telegram photo.

Login pages, age gates, and bot challenges fail instead of being solved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from herald.config import Settings
from herald.errors import AuthRequired, ChallengeDetected, ParseFailure
from herald.http import HttpClient
from herald.models import ActorHit, FetchResult, FoundItem, Subscription
from herald.pageguard import looks_like_age_gate, looks_like_challenge, looks_like_login
from herald.routing import ACTOR_RESERVED, require_actor_id
from herald.util import clip, collapse, hostname_of, is_clean_https, same_site, usable_cover_url

CODE_RE = re.compile(
    r"(?<![A-Z0-9])((?:FC2-PPV|HEYZO|[A-Z][A-Z0-9]{1,11})-\d{2,7})(?!\d)",
    re.IGNORECASE,
)
V_RE = re.compile(r"/v/([A-Za-z0-9]{2,32})")
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
COUNT_RE = re.compile(r"(\d+)\s*部影片")
EMPTY_MARKERS = ("暂无内容", "暫無內容", "暂无", "暫無")
MAX_WORKS = 50


@dataclass(frozen=True)
class ActorWork:
    code: str | None
    v_id: str
    title: str
    published_at: str | None
    url: str
    cover_url: str | None = None


@dataclass(frozen=True)
class ActorPage:
    name: str | None
    declared_count: int | None
    works: list[ActorWork]
    empty: bool


class JavdbProvider:
    name = "javdb"

    def fetch(self, sub: Subscription, client: HttpClient, settings: Settings) -> FetchResult:
        actor_id = require_actor_id(sub.target)
        page_url = f"{settings.javdb_base}/actors/{actor_id}"
        headers = {}
        cookie = settings.secrets.get("JAVDB_COOKIE")
        if cookie:
            headers["Cookie"] = cookie
        html, final = client.get_text(page_url, headers=headers or None)
        _ensure_same_site(settings.javdb_base, final)
        _pause_if_challenge(client, html, final)
        parsed = parse_actor_page(html, final, had_cookie=bool(cookie))
        items = [_work_item(work) for work in parsed.works]
        return FetchResult(items=items, display_name=parsed.name, canonical_url=page_url)


def search_actors(client: HttpClient, settings: Settings, name: str) -> list[ActorHit]:
    query = urlencode({"f": "actor", "q": name})
    page_url = f"{settings.javdb_base}/search?{query}"
    headers = {}
    cookie = settings.secrets.get("JAVDB_COOKIE")
    if cookie:
        headers["Cookie"] = cookie
    html, final = client.get_text(page_url, headers=headers or None)
    _ensure_same_site(settings.javdb_base, final)
    _pause_if_challenge(client, html, final)
    return parse_actor_search(html, final, had_cookie=bool(cookie))


def parse_actor_page(html: str, page_url: str, *, had_cookie: bool = False) -> ActorPage:
    if looks_like_challenge(html, page_url):
        raise ChallengeDetected(
            "JavDB returned a bot challenge or a block page. "
            "Herald does not solve challenges and will not retry this host in this run."
        )
    soup = BeautifulSoup(html, "html.parser")
    visible = soup.get_text(" ", strip=True)
    declared = _declared_count(visible)
    works = _works(soup, page_url)
    empty = declared == 0 or any(marker in visible for marker in EMPTY_MARKERS)
    name = _page_name(soup)
    if not works:
        _raise_for_empty_wall(html, page_url, had_cookie=had_cookie)
    if declared is not None and declared > 0 and not works:
        raise ParseFailure(
            "JavDB page says there are works, but no work links could be read. "
            "The HTML layout may have changed, or this was not an actor page."
        )
    if not works and not empty and name is None:
        raise ParseFailure("could not read a JavDB actor page from the response")
    return ActorPage(name=name, declared_count=declared, works=works, empty=empty and not works)


def parse_actor_search(html: str, page_url: str, *, had_cookie: bool = False) -> list[ActorHit]:
    if looks_like_challenge(html, page_url):
        raise ChallengeDetected(
            "JavDB returned a bot challenge or a block page. Herald does not solve challenges."
        )
    soup = BeautifulSoup(html, "html.parser")
    origin = _origin(page_url)
    hits: list[ActorHit] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        actor_id = _actor_id_from_href(anchor["href"])
        if actor_id is None or actor_id in seen:
            continue
        strong = anchor.find("strong")
        name = collapse(strong.get_text(" ", strip=True)) if strong else ""
        if not name:
            name = collapse(anchor.get_text(" ", strip=True))
        if not name or len(name) > 80:
            continue
        aliases = collapse(anchor.get("title") or "")
        blob = f"{name} {aliases} {collapse(anchor.get_text(' ', strip=True))}"
        marker = ""
        if "無碼" in blob or "无码" in blob:
            marker = "uncensored"
        elif "有碼" in blob or "有码" in blob:
            marker = "censored"
        seen.add(actor_id)
        hits.append(
            ActorHit(
                actor_id=actor_id,
                name=name,
                aliases=aliases,
                marker=marker,
                url=f"{origin}/actors/{actor_id}",
            )
        )
    if not hits:
        _raise_for_empty_wall(html, page_url, had_cookie=had_cookie)
    return hits


def _raise_for_empty_wall(html: str, final_url: str, *, had_cookie: bool) -> None:
    if looks_like_login(html, final_url):
        hint = (
            "The saved JavDB cookie was not accepted."
            if had_cookie
            else "Set JAVDB_COOKIE only if you already passed the gate in your own browser."
        )
        raise AuthRequired(f"JavDB returned a login page. {hint} Herald will not sign up or bypass it.")
    if looks_like_age_gate(html):
        hint = (
            "The saved JavDB cookie was not accepted."
            if had_cookie
            else "Set JAVDB_COOKIE to a cookie from your own browser, or pick a page that is public."
        )
        raise AuthRequired(f"JavDB returned an age gate. {hint}")


def _pause_if_challenge(client: HttpClient, html: str, final_url: str) -> None:
    if not looks_like_challenge(html, final_url):
        return
    message = (
        "JavDB returned a bot challenge or a block page. "
        "Herald does not solve challenges and will not retry this host in this run."
    )
    client.pause_host(final_url, message)
    raise ChallengeDetected(message)


def _ensure_same_site(base: str, final_url: str) -> None:
    expected = hostname_of(base)
    actual = hostname_of(final_url)
    if not same_site(expected, actual):
        raise ChallengeDetected(
            f"JavDB redirected to {actual or 'another host'}, which is not the configured base. "
            "Herald will not follow that."
        )


def _works(soup: BeautifulSoup, page_url: str) -> list[ActorWork]:
    works: list[ActorWork] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        match = V_RE.search(anchor["href"])
        if not match or len(works) >= MAX_WORKS:
            continue
        v_id = match.group(1)
        code = _code_from_anchor(anchor)
        key = code or f"v:{v_id}"
        if key in seen:
            continue
        text = collapse(anchor.get_text(" ", strip=True))
        published = _date(text)
        title = _title(text, code, published, anchor.get("title") or "")
        url = urljoin(page_url, f"/v/{v_id}")
        if not is_clean_https(url):
            continue
        seen.add(key)
        works.append(
            ActorWork(
                code=code,
                v_id=v_id,
                title=title,
                published_at=published,
                url=url,
                cover_url=_cover_from_anchor(anchor, page_url),
            )
        )
    return works


def _code_from_anchor(anchor) -> str | None:
    strong = anchor.find("strong")
    if strong is not None:
        code = extract_code(strong.get_text(" ", strip=True))
        if code:
            return code
    titled = extract_code(anchor.get("title") or "")
    if titled:
        return titled
    return extract_code(anchor.get_text(" ", strip=True))


def extract_code(text: str) -> str | None:
    match = CODE_RE.search(text.upper())
    if not match:
        return None
    return match.group(1).upper()


def _date(text: str) -> str | None:
    match = DATE_RE.search(text)
    return match.group(1) if match else None


def _title(text: str, code: str | None, published: str | None, title_attr: str) -> str:
    body = text
    if code and body.upper().startswith(code):
        body = body[len(code) :].strip(" -|")
    if published and published in body:
        body = body.replace(published, "").strip(" -|")
    body = collapse(body)
    if not body:
        body = collapse(title_attr)
        if code and body.upper().startswith(code):
            body = body[len(code) :].strip(" -|")
    if code and body:
        return clip(f"{code} {body}")
    return clip(code or body or "(untitled)")


def _declared_count(visible: str) -> int | None:
    match = COUNT_RE.search(visible)
    if not match:
        return None
    return int(match.group(1))


def _page_name(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all(["h1", "h2"]):
        text = collapse(tag.get_text(" ", strip=True))
        if text and len(text) <= 80:
            return text
    if soup.title is not None:
        text = collapse(soup.title.get_text())
        head = re.split(r"[|\-–—]", text)[0].strip()
        if head and len(head) <= 80:
            return head
    return None


def _actor_id_from_href(href: str) -> str | None:
    path = urlsplit(href).path
    pieces = [piece for piece in path.split("/") if piece]
    if len(pieces) != 2 or pieces[0].lower() != "actors":
        return None
    actor_id = pieces[1]
    if not re.fullmatch(r"[A-Za-z0-9]{2,32}", actor_id):
        return None
    if actor_id.lower() in ACTOR_RESERVED:
        return None
    return actor_id


def _origin(page_url: str) -> str:
    parts = urlsplit(page_url)
    return f"https://{parts.hostname}"


def _cover_from_anchor(anchor, page_url: str) -> str | None:
    image = anchor.find("img")
    if image is None:
        return None
    raw = image.get("data-src") or image.get("data-original") or image.get("src") or ""
    if not raw or raw.startswith("data:"):
        return None
    return usable_cover_url(urljoin(page_url, raw))


def _work_item(work: ActorWork) -> FoundItem:
    key = work.code or f"v:{work.v_id}"
    return FoundItem(
        key=key,
        title=work.title,
        url=work.url,
        published_at=work.published_at,
        cover_url=work.cover_url,
    )
