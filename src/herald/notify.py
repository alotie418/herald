"""Render notices and send them through the Telegram Bot API."""

from __future__ import annotations

import json

import httpx

from herald.errors import CoverUnavailable, HeraldError
from herald.models import PendingNotice
from herald.util import clip, is_clean_https, usable_cover_url

TELEGRAM_LIMIT = 3500
CAPTION_LIMIT = 1024
MAX_PHOTO_BYTES = 5_000_000
PHOTO_REJECT_STATUSES = {400, 413, 415}
SITE_LABELS = {
    "javdb": "JavDB",
    "kemono": "Kemono",
    "coomer": "Coomer",
    "fanbox": "FANBOX",
    "patreon": "Patreon",
    "subscribestar": "SubscribeStar",
    "simpcity": "SimpCity",
    "rule34": "rule34.xxx",
    "f95": "F95zone",
}
IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def site_label(provider: str) -> str:
    return SITE_LABELS.get(provider, provider)


def render_block(item: PendingNotice) -> str:
    who = clip(item.label, 80)
    title = clip(item.item_title, 300)
    url = item.item_url.strip()
    if item.kind == "feed" and not item.item_key.startswith("v:") and not title.startswith(item.item_key):
        head = f"{item.item_key} {title}".strip()
    else:
        head = title
    return f"[{site_label(item.provider)}] {who}\n{head}\n{url}"


def render_caption(item: PendingNotice) -> str:
    url = item.item_url.strip()
    text = f"Herald\n\n{render_block(item)}"
    if len(text) <= CAPTION_LIMIT:
        return text
    if url and len(url) + 1 < CAPTION_LIMIT:
        room = CAPTION_LIMIT - len(url) - 1
        head = clip(text.replace(url, " ").strip(), room)
        return f"{head}\n{url}"
    return clip(text, CAPTION_LIMIT)


def render_messages(pending: list[PendingNotice]) -> list[tuple[str, list[int]]]:
    if not pending:
        return []
    groups: list[list[tuple[str, int]]] = []
    current: list[tuple[str, int]] = []
    size = 0
    for item in pending:
        block = render_block(item)
        if len(block) > TELEGRAM_LIMIT:
            if current:
                groups.append(current)
                current = []
                size = 0
            for piece in _hard_split(block, TELEGRAM_LIMIT):
                groups.append([(piece, item.seen_id)])
            continue
        extra = len(block) + (2 if current else 0)
        if current and size + extra > TELEGRAM_LIMIT:
            groups.append(current)
            current = []
            size = 0
            extra = len(block)
        current.append((block, item.seen_id))
        size += extra
    if current:
        groups.append(current)

    total = len(groups)
    messages: list[tuple[str, list[int]]] = []
    for index, group in enumerate(groups, start=1):
        header = "Herald" if total == 1 else f"Herald ({index}/{total})"
        body = "\n\n".join(block for block, _seen in group)
        ids = [seen for _block, seen in group]
        messages.append((f"{header}\n\n{body}", ids))
    return messages


def _hard_split(text: str, limit: int) -> list[str]:
    return [text[start : start + limit] for start in range(0, len(text), limit)] or [text]


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30,
        trust_env: bool = True,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            trust_env=trust_env,
            follow_redirects=True,
            max_redirects=5,
        )

    def close(self) -> None:
        self._client.close()

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            response = self._client.post(
                url,
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            raise HeraldError(f"Telegram request failed ({type(exc).__name__})") from exc
        self._raise_for_status(response, photo=False)

    def fetch_cover(self, url: str) -> tuple[bytes, str]:
        if usable_cover_url(url) is None or not is_clean_https(url):
            raise CoverUnavailable("cover URL is not a plain https URL")
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            raise CoverUnavailable(f"cover image was not available ({type(exc).__name__})") from exc
        final = str(response.url)
        if usable_cover_url(final) is None:
            raise CoverUnavailable("cover image redirected off a plain https URL")
        if response.status_code != 200:
            raise CoverUnavailable(f"cover image returned HTTP {response.status_code}")
        data = response.content
        if not data or len(data) > MAX_PHOTO_BYTES:
            raise CoverUnavailable("cover image is missing or too large to send")
        mime = _image_type(data)
        if mime is None:
            raise CoverUnavailable("cover response was not an image")
        return data, mime

    def send_photo_bytes(self, caption: str, data: bytes, mime: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendPhoto"
        filename = f"cover.{IMAGE_TYPES.get(mime, 'img')}"
        try:
            response = self._client.post(
                url,
                data={"chat_id": self._chat_id, "caption": caption},
                files={"photo": (filename, data, mime)},
            )
        except httpx.HTTPError as exc:
            raise HeraldError(f"Telegram request failed ({type(exc).__name__})") from exc
        self._raise_for_status(response, photo=True)

    def send_media_group(self, photos: list[tuple[str, bytes, str]]) -> None:
        if not photos:
            return
        if len(photos) == 1:
            caption, data, mime = photos[0]
            self.send_photo_bytes(caption, data, mime)
            return
        url = f"https://api.telegram.org/bot{self._token}/sendMediaGroup"
        media = []
        files = {}
        for index, (caption, data, mime) in enumerate(photos):
            key = f"f{index}"
            filename = f"cover{index}.{IMAGE_TYPES.get(mime, 'img')}"
            media.append({"type": "photo", "media": f"attach://{key}", "caption": caption})
            files[key] = (filename, data, mime)
        try:
            response = self._client.post(
                url,
                data={"chat_id": self._chat_id, "media": json.dumps(media, ensure_ascii=False)},
                files=files,
            )
        except httpx.HTTPError as exc:
            raise HeraldError(f"Telegram request failed ({type(exc).__name__})") from exc
        self._raise_for_status(response, photo=True)

    def _raise_for_status(self, response: httpx.Response, *, photo: bool) -> None:
        if response.status_code == 200:
            return
        description = ""
        try:
            description = str(response.json().get("description") or "")
        except Exception:
            description = ""
        description = description.replace(self._token, "[redacted]")
        message = f"Telegram returned HTTP {response.status_code} {description}".strip()
        if photo and response.status_code in PHOTO_REJECT_STATUSES:
            raise CoverUnavailable(message)
        raise HeraldError(message)


def _image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None
