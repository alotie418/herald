import json

import httpx

from herald.bot import HELP, handle_text, handle_update, run_bot
from herald.cli import main
from herald.config import data_dir, load_settings
from herald.db import Database
from herald.errors import HeraldError
from herald.lockfile import file_lock
from herald.models import ActorHit
from herald.notify import TelegramNotifier


def _settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345:secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    return load_settings()


def _hits():
    return [
        ActorHit("Ab12Cd", "示例演员", "示例演员, Example", "censored", "https://javdb.com/actors/Ab12Cd"),
        ActorHit("Ef34Gh", "示例演员", "示例演员", "uncensored", "https://javdb.com/actors/Ef34Gh"),
    ]


def _db(isolated) -> Database:
    return Database(isolated / "data" / "herald.db")


def test_one_hit_subscribes_without_checking(monkeypatch, isolated):
    settings = _settings(monkeypatch)
    db = _db(isolated)
    seen = []

    def search(name: str):
        seen.append(name)
        return [_hits()[0]]

    reply = handle_text(" 示例演员 ", settings=settings, db=db, search=search)
    sub = db.find("feed", "javdb", "Ab12Cd")
    assert sub is not None
    assert sub.baselined is False
    assert sub.title == "示例演员"
    assert "已订阅" in reply
    assert sub.url in reply
    assert seen == ["示例演员"]
    assert db.list_search() == []
    again = handle_text("示例演员", settings=settings, db=db, search=search)
    assert "已经订阅" in again
    db.close()


def test_multiple_hits_wait_for_a_number(monkeypatch, isolated):
    settings = _settings(monkeypatch)
    db = _db(isolated)

    def search(_name: str):
        return _hits()

    listed = handle_text("示例演员", settings=settings, db=db, search=search)
    assert "找到 2 个结果" in listed
    assert "Ab12Cd" in listed
    assert "Ef34Gh" in listed
    assert "不会自动选择" in listed
    assert db.find("feed", "javdb", "Ab12Cd") is None
    picked = handle_text("2", settings=settings, db=db, search=search)
    assert "Ef34Gh" in picked
    assert db.find("feed", "javdb", "Ef34Gh") is not None
    assert db.find("feed", "javdb", "Ab12Cd") is None
    db.close()


def test_cancel_drops_the_pending_choice(monkeypatch, isolated):
    settings = _settings(monkeypatch)
    db = _db(isolated)
    handle_text("示例演员", settings=settings, db=db, search=lambda _name: _hits())
    assert "已取消选择" in handle_text("/cancel", settings=settings, db=db, search=lambda _name: _hits())
    reply = handle_text("1", settings=settings, db=db, search=lambda _name: _hits())
    assert "没有待选结果" in reply
    assert db.list_subscriptions() == []
    db.close()


def test_commands_and_other_chats(monkeypatch, isolated):
    settings = _settings(monkeypatch)
    db = _db(isolated)
    db.add_subscription(
        kind="feed",
        provider="javdb",
        target="Ab12Cd",
        url="https://javdb.com/actors/Ab12Cd",
        title="示例演员",
        now="2026-01-01T00:00:00Z",
    )
    db.set_enabled(1, False)
    listed = handle_text("/list@HeraldBot", settings=settings, db=db, search=lambda _name: [])
    assert "#1 示例演员（已暂停）" in listed
    assert "https://javdb.com/actors/Ab12Cd" in listed
    assert "/list" in handle_text("/help", settings=settings, db=db, search=lambda _name: [])
    assert handle_text("/start", settings=settings, db=db, search=lambda _name: []) == HELP
    ignored = handle_update(
        {"update_id": 1, "message": {"chat": {"id": 99}, "text": "示例演员"}},
        settings=settings,
        db=db,
        search=lambda _name: _hits(),
    )
    assert ignored is None
    assert db.find("feed", "javdb", "Ef34Gh") is None
    db.close()


def test_search_failure_is_a_reply(monkeypatch, isolated):
    settings = _settings(monkeypatch)
    db = _db(isolated)

    def search(_name: str):
        raise HeraldError("JavDB returned a login page")

    reply = handle_text("示例演员", settings=settings, db=db, search=search)
    assert "搜索失败" in reply
    assert "login" in reply
    assert db.list_subscriptions() == []
    db.close()


def test_long_poll_replies_only_to_the_configured_chat(monkeypatch, isolated):
    settings = _settings(monkeypatch)
    db = _db(isolated)
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/getUpdates"):
            assert body["allowed_updates"] == ["message"]
            assert body["timeout"] == 0
            if body.get("offset") == 8:
                return httpx.Response(200, json={"ok": True, "result": []})
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {
                            "update_id": 7,
                            "message": {"chat": {"id": 7}, "text": "别人"},
                        }
                    ],
                },
            )
        assert request.url.path.endswith("/sendMessage")
        assert body["chat_id"] == "42"
        assert body["disable_web_page_preview"] is True
        assert "secret-token" not in body["text"]
        sent.append(body["text"])
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier(
        "12345:secret-token",
        "42",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    calls = []

    def search(name: str):
        calls.append(name)
        return [_hits()[0]]

    run_bot(
        settings,
        db,
        notifier,
        search,
        poll_timeout=0,
        max_polls=2,
    )
    notifier.close()
    assert calls == []
    assert sent == []
    assert db.list_subscriptions() == []

    def handler_ok(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/getUpdates"):
            if body.get("offset"):
                return httpx.Response(200, json={"ok": True, "result": []})
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {"update_id": 3, "message": {"chat": {"id": 42}, "text": "示例演员"}}
                    ],
                },
            )
        sent.append(body["text"])
        return httpx.Response(200, json={"ok": True})

    notifier = TelegramNotifier(
        "12345:secret-token",
        "42",
        transport=httpx.MockTransport(handler_ok),
        trust_env=False,
    )
    run_bot(settings, db, notifier, search, poll_timeout=0, max_polls=1)
    notifier.close()
    assert calls == ["示例演员"]
    assert any("已订阅" in text for text in sent)
    assert db.find("feed", "javdb", "Ab12Cd").baselined is False
    db.close()


def test_bot_requires_telegram_and_a_second_process_skips(monkeypatch, capsys, isolated):
    assert main(["bot"]) == 2
    assert "chat id" in capsys.readouterr().err
    _settings(monkeypatch)
    with file_lock(data_dir() / "bot.lock", message="another herald bot is already running"):
        assert main(["bot"]) == 0
    err = capsys.readouterr().err
    assert "skip" in err
    assert "bot" in err
    assert isolated is not None
