import httpx

from herald.check import format_report, run_check
from herald.config import load_settings
from herald.db import Database
from herald.errors import HeraldError
from herald.http import HttpClient
from herald.limiter import HostLimiter
from herald.models import FetchResult, FoundItem
from herald.notify import TelegramNotifier
from herald.providers.registry import get_provider
from tests.conftest import subscription


class FakeProvider:
    def __init__(self, items, error=None):
        self.items = items
        self.error = error
        self.calls = 0

    def fetch(self, sub, client, settings):
        self.calls += 1
        if self.error:
            raise self.error
        return FetchResult(items=list(self.items), display_name="Ada")


class RecordingNotifier:
    def __init__(self):
        self.messages = []

    def send(self, text: str) -> None:
        self.messages.append(text)


def _db(path) -> Database:
    return Database(path)


def _client() -> HttpClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("check should not make HTTP calls for a fake provider")

    return HttpClient(
        timeout=5,
        user_agent="herald-test",
        limiter=HostLimiter(0, 0, {}),
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )


def test_first_check_baselines_and_second_check_notifies(isolated):
    db = _db(isolated / "data" / "herald.db")
    db.add_subscription(
        kind="follow",
        provider="kemono",
        target="patreon:42@kemono.cr",
        url="https://kemono.cr/patreon/user/42",
        title=None,
        now="2026-01-01T00:00:00Z",
    )
    provider = FakeProvider(
        [FoundItem("1", "旧草稿", "https://kemono.cr/patreon/user/42/post/1")]
    )
    notifier = RecordingNotifier()
    settings = load_settings()
    with _client() as client:
        first = run_check(
            settings,
            db,
            client,
            notifier=notifier,
            providers={"kemono": provider},
            now="2026-02-01T00:00:00Z",
        )
        provider.items.append(FoundItem("2", "素描近况", "https://kemono.cr/patreon/user/42/post/2"))
        second = run_check(
            settings,
            db,
            client,
            notifier=notifier,
            providers={"kemono": provider},
            now="2026-02-02T00:00:00Z",
        )
        third = run_check(
            settings,
            db,
            client,
            notifier=notifier,
            providers={"kemono": provider},
            now="2026-02-03T00:00:00Z",
        )
    assert first.baselines[0][2] == 1
    assert first.fresh == []
    assert first.notified == 0
    assert db.get(1).baselined is True
    assert db.get(1).title == "Ada"
    assert len(second.fresh) == 1
    assert second.notified == 1
    assert "素描近况" in notifier.messages[0]
    assert "https://kemono.cr/patreon/user/42/post/2" in notifier.messages[0]
    assert third.fresh == []
    assert third.notified == 0
    assert len(notifier.messages) == 1
    text = format_report(second)
    assert "new" in text
    db.close()


def test_dry_run_does_not_write(isolated):
    db = _db(isolated / "data" / "herald.db")
    db.add_subscription(
        kind="follow",
        provider="kemono",
        target="patreon:42@kemono.cr",
        url="https://kemono.cr/patreon/user/42",
        title="Ada",
        now="2026-01-01T00:00:00Z",
    )
    provider = FakeProvider([FoundItem("1", "旧草稿", "https://kemono.cr/patreon/user/42/post/1")])
    with _client() as client:
        report = run_check(
            load_settings(),
            db,
            client,
            notifier=RecordingNotifier(),
            providers={"kemono": provider},
            dry_run=True,
        )
    assert report.dry_run is True
    assert report.baselines
    assert db.get(1).baselined is False
    assert db.has_seen(1, "1") is False
    db.close()


def test_missing_telegram_keeps_items_queued(isolated):
    db = _db(isolated / "data" / "herald.db")
    db.add_subscription(
        kind="follow",
        provider="kemono",
        target="patreon:42@kemono.cr",
        url="https://kemono.cr/patreon/user/42",
        title="Ada",
        now="2026-01-01T00:00:00Z",
    )
    provider = FakeProvider([FoundItem("1", "旧草稿", "https://kemono.cr/patreon/user/42/post/1")])
    with _client() as client:
        report = run_check(
            load_settings(),
            db,
            client,
            notifier=None,
            providers={"kemono": provider},
            notify_existing=True,
        )
    assert report.telegram_missing is True
    assert report.exit_code == 2
    assert db.pending_notices()
    db.close()


def test_failed_send_is_retried_later(isolated):
    db = _db(isolated / "data" / "herald.db")
    db.add_subscription(
        kind="follow",
        provider="kemono",
        target="patreon:42@kemono.cr",
        url="https://kemono.cr/patreon/user/42",
        title="Ada",
        now="2026-01-01T00:00:00Z",
    )
    provider = FakeProvider([FoundItem("1", "旧草稿", "https://kemono.cr/patreon/user/42/post/1")])

    class Boom:
        def send(self, text: str) -> None:
            raise HeraldError("Telegram returned HTTP 500")

    with _client() as client:
        failed = run_check(
            load_settings(),
            db,
            client,
            notifier=Boom(),
            providers={"kemono": provider},
            notify_existing=True,
        )
        notifier = RecordingNotifier()
        retried = run_check(
            load_settings(),
            db,
            client,
            notifier=notifier,
            providers={"kemono": provider},
        )
    assert failed.telegram_error
    assert failed.exit_code == 2
    assert retried.notified == 1
    assert notifier.messages
    assert db.pending_notices() == []
    db.close()


def test_paused_subscription_is_not_fetched(isolated):
    db = _db(isolated / "data" / "herald.db")
    sub, _created = db.add_subscription(
        kind="follow",
        provider="kemono",
        target="patreon:42@kemono.cr",
        url="https://kemono.cr/patreon/user/42",
        title="Ada",
        now="2026-01-01T00:00:00Z",
    )
    db.set_enabled(sub.id, False)
    provider = FakeProvider([])
    with _client() as client:
        run_check(load_settings(), db, client, notifier=None, providers={"kemono": provider})
    assert provider.calls == 0
    db.close()


def test_real_kemono_check_with_mocked_http_and_telegram(isolated):
    posts = [
        {
            "id": "1",
            "user": "42",
            "service": "patreon",
            "title": "旧草稿",
            "attachments": [{"path": "/data/skip.jpg"}],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "/data/" in request.url.path:
            raise AssertionError("media was requested")
        return httpx.Response(200, json=list(posts))

    db = _db(isolated / "data" / "herald.db")
    db.add_subscription(
        kind="follow",
        provider="kemono",
        target="patreon:42@kemono.cr",
        url="https://kemono.cr/patreon/user/42",
        title=None,
        now="2026-01-01T00:00:00Z",
    )
    sent = []

    def telegram(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        sent.append(payload["text"])
        assert "12345:secret-token" not in payload["text"]
        return httpx.Response(200, json={"ok": True})

    settings = load_settings()
    client = HttpClient(
        timeout=5,
        user_agent="herald-test",
        limiter=HostLimiter(0, 0, {}),
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    notifier = TelegramNotifier(
        "12345:secret-token",
        "99",
        transport=httpx.MockTransport(telegram),
        trust_env=False,
    )
    first = run_check(settings, db, client, notifier=notifier, providers={"kemono": get_provider("kemono")})
    posts.append({"id": "2", "user": "42", "service": "patreon", "title": "新速写"})
    second = run_check(settings, db, client, notifier=notifier, providers={"kemono": get_provider("kemono")})
    client.close()
    notifier.close()
    assert first.notified == 0
    assert second.notified == 1
    assert "新速写" in sent[0]
    assert "/data/skip.jpg" not in sent[0]
    db.close()


def test_block_status_pauses_the_rest_of_the_host(isolated):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(403, text="no")

    db = _db(isolated / "data" / "herald.db")
    for user in ("1", "2"):
        db.add_subscription(
            kind="follow",
            provider="kemono",
            target=f"patreon:{user}@kemono.cr",
            url=f"https://kemono.cr/patreon/user/{user}",
            title=None,
            now="2026-01-01T00:00:00Z",
        )
    client = HttpClient(
        timeout=5,
        user_agent="herald-test",
        limiter=HostLimiter(0, 0, {}),
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    report = run_check(
        load_settings(),
        db,
        client,
        notifier=None,
        providers={"kemono": get_provider("kemono")},
    )
    client.close()
    assert len(calls) == 1
    assert len(report.errors) == 2
    assert report.exit_code == 1
    assert "paused" in report.errors[1]
    assert db.get(1).baselined is False
    db.close()


def test_subscription_object_used_by_helpers():
    assert subscription().provider == "kemono"


JPEG = b"\xff\xd8\xff" + b"\x00" * 80


def test_javdb_cover_sends_a_photo_and_a_bare_item_stays_text(isolated):
    db = _db(isolated / "data" / "herald.db")
    db.add_subscription(
        kind="feed",
        provider="javdb",
        target="Ab12Cd",
        url="https://javdb.com/actors/Ab12Cd",
        title="示例演员",
        now="2026-01-01T00:00:00Z",
    )
    provider = FakeProvider(
        [
            FoundItem(
                "ABCD-001",
                "ABCD-001 第一部示例",
                "https://javdb.com/v/alpha01",
                cover_url="https://images.example.test/covers/alpha01.jpg",
            ),
            FoundItem("ZZZZ-002", "ZZZZ-002 没有封面", "https://javdb.com/v/plain"),
        ]
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.example.test":
            calls.append(("image", request.url.path))
            return httpx.Response(200, content=JPEG)
        calls.append(("telegram", request.url.path, request.content))
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier("12345:secret-token", "99", transport=httpx.MockTransport(handler), trust_env=False)
    with _client() as client:
        report = run_check(
            load_settings(),
            db,
            client,
            notifier=notifier,
            providers={"javdb": provider},
            notify_existing=True,
        )
    notifier.close()
    assert report.notified == 2
    assert report.exit_code == 0
    assert calls[0][0] == "image"
    telegram = [item for item in calls if item[0] == "telegram"]
    photo = next(body for _kind, path, body in telegram if path.endswith("/sendPhoto"))
    text = next(body for _kind, path, body in telegram if path.endswith("/sendMessage"))
    assert "示例演员".encode() in photo
    assert b"JavDB" in photo
    assert b"ABCD-001" in photo
    assert b"https://javdb.com/v/alpha01" in photo
    assert JPEG in photo
    assert b"disable_web_page_preview" not in photo
    import json

    payload = json.loads(text)
    assert payload["disable_web_page_preview"] is True
    assert "ZZZZ-002" in payload["text"]
    assert "https://javdb.com/v/plain" in payload["text"]
    stored = (isolated / "data" / "herald.db").read_bytes()
    assert JPEG not in stored
    assert b"https://images.example.test/covers/alpha01.jpg" in stored
    db.close()


def test_missing_cover_image_falls_back_to_text(isolated):
    db = _db(isolated / "data" / "herald.db")
    db.add_subscription(
        kind="feed",
        provider="javdb",
        target="Ab12Cd",
        url="https://javdb.com/actors/Ab12Cd",
        title="示例演员",
        now="2026-01-01T00:00:00Z",
    )
    provider = FakeProvider(
        [
            FoundItem(
                "ABCD-009",
                "ABCD-009 封面失效",
                "https://javdb.com/v/gone",
                cover_url="https://images.example.test/covers/gone.jpg",
            )
        ]
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.example.test":
            return httpx.Response(404, text="missing")
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "result": {"text": request.read().decode()}})

    notifier = TelegramNotifier("1:abc", "2", transport=httpx.MockTransport(handler), trust_env=False)
    with _client() as client:
        report = run_check(
            load_settings(),
            db,
            client,
            notifier=notifier,
            providers={"javdb": provider},
            notify_existing=True,
        )
    notifier.close()
    assert report.notified == 1
    assert calls == ["/bot1:abc/sendMessage"]
    db.close()


def test_several_covers_go_out_as_one_media_group(isolated):
    db = _db(isolated / "data" / "herald.db")
    db.add_subscription(
        kind="follow",
        provider="fanbox",
        target="ada",
        url="https://www.fanbox.cc/@ada",
        title="Ada",
        now="2026-01-01T00:00:00Z",
    )
    provider = FakeProvider(
        [
            FoundItem(
                "10",
                "公开草稿",
                "https://www.fanbox.cc/@ada/posts/10",
                cover_url="https://images.example.test/a.jpg",
            ),
            FoundItem(
                "11",
                "另一张",
                "https://www.fanbox.cc/@ada/posts/11",
                cover_url="https://images.example.test/b.jpg",
            ),
        ]
    )
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "images.example.test":
            return httpx.Response(200, content=JPEG)
        paths.append(request.url.path)
        assert "公开草稿".encode() in request.content
        assert "另一张".encode() in request.content
        assert b"FANBOX" in request.content
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier("1:abc", "2", transport=httpx.MockTransport(handler), trust_env=False)
    with _client() as client:
        report = run_check(
            load_settings(),
            db,
            client,
            notifier=notifier,
            providers={"fanbox": provider},
            notify_existing=True,
        )
    notifier.close()
    assert report.notified == 2
    assert paths == ["/bot1:abc/sendMediaGroup"]
    db.close()
