import httpx
import pytest

from herald.errors import HeraldError
from herald.models import PendingNotice
from herald.notify import TelegramNotifier, render_messages


def _notice(seen_id: int, title: str) -> PendingNotice:
    return PendingNotice(
        seen_id=seen_id,
        subscription_id=1,
        kind="feed",
        provider="javdb",
        target="Ab12",
        sub_title="示例演员",
        item_key="ABCD-001",
        item_title=title,
        item_url=f"https://javdb.com/v/{seen_id}",
    )


def test_long_notices_split_without_dropping_ids():
    pending = [_notice(index, "作品" * 400) for index in range(1, 16)]
    messages = render_messages(pending)
    assert len(messages) >= 2
    seen = []
    for text, ids in messages:
        assert text.startswith("Herald")
        assert len(text) < 4096
        seen.extend(ids)
    assert seen == list(range(1, 16))


def test_telegram_error_does_not_echo_the_token():
    token = "999:LEAKTOKEN"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": f"bad {token}"})

    notifier = TelegramNotifier(token, "1", transport=httpx.MockTransport(handler), trust_env=False)
    with pytest.raises(HeraldError) as caught:
        notifier.send("hello")
    notifier.close()
    assert token not in str(caught.value)
    assert "[redacted]" in str(caught.value)


def test_preview_fetch_is_disabled():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier("1:abc", "2", transport=httpx.MockTransport(handler), trust_env=False)
    notifier.send("标题\nhttps://javdb.com/v/alpha")
    notifier.close()
    import json

    assert json.loads(seen["body"])["disable_web_page_preview"] is True


JPEG = b"\xff\xd8\xff" + b"\x00" * 64


def test_photo_upload_uses_sendphoto_and_drops_the_preview_flag():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.example.test":
            return httpx.Response(200, content=JPEG)
        seen["path"] = request.url.path
        seen["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier("1:abc", "42", transport=httpx.MockTransport(handler), trust_env=False)
    data, mime = notifier.fetch_cover("https://images.example.test/covers/alpha01.jpg")
    caption = "Herald\n\n[JavDB] 示例演员\nABCD-001 第一部示例\nhttps://javdb.com/v/alpha01"
    notifier.send_photo_bytes(caption, data, mime)
    notifier.close()
    assert seen["path"].endswith("/sendPhoto")
    assert b"chat_id" in seen["body"]
    assert "示例演员".encode() in seen["body"]
    assert b"ABCD-001" in seen["body"]
    assert b"https://javdb.com/v/alpha01" in seen["body"]
    assert b"JavDB" in seen["body"]
    assert JPEG in seen["body"]
    assert b"disable_web_page_preview" not in seen["body"]
    assert mime == "image/jpeg"


def test_rejected_photo_is_a_cover_failure_and_hides_the_token():
    token = "999:LEAKTOKEN"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.telegram.org":
            return httpx.Response(200, content=JPEG)
        return httpx.Response(400, json={"ok": False, "description": f"bad photo {token}"})

    notifier = TelegramNotifier(token, "1", transport=httpx.MockTransport(handler), trust_env=False)
    from herald.errors import CoverUnavailable

    with pytest.raises(CoverUnavailable) as caught:
        notifier.send_photo_bytes("caption", JPEG, "image/jpeg")
    notifier.close()
    assert token not in str(caught.value)
    assert "[redacted]" in str(caught.value)


def test_html_body_is_not_sent_as_a_photo():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>just a moment</html>")

    notifier = TelegramNotifier("1:abc", "1", transport=httpx.MockTransport(handler), trust_env=False)
    from herald.errors import CoverUnavailable

    with pytest.raises(CoverUnavailable, match="not an image"):
        notifier.fetch_cover("https://images.example.test/covers/alpha01.jpg")
    notifier.close()
