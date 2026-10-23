import httpx

from herald.cli import main
from herald.config import load_settings
from herald.models import ActorHit


def test_follow_add_list_pause_and_remove(capsys):
    url = "https://kemono.cr/patreon/user/42"
    assert main(["follow", "add", url, "--title", "Ada"]) == 0
    assert main(["follow", "add", url, "--title", "Ada Two"]) == 0
    assert main(["follow", "list"]) == 0
    listed = capsys.readouterr().out
    assert "Ada Two" in listed
    assert "already following" in listed
    assert main(["follow", "pause", "1"]) == 0
    assert main(["follow", "resume", "1"]) == 0
    assert main(["follow", "remove", url]) == 0
    assert main(["follow", "list"]) == 0
    assert "no follow subscriptions" in capsys.readouterr().out


def test_feed_search_does_not_auto_subscribe(monkeypatch, capsys):
    hits = [
        ActorHit("Ab12Cd", "示例演员", "示例演员, Example", "censored", "https://javdb.com/actors/Ab12Cd"),
        ActorHit("Ef34Gh", "示例演员", "示例演员", "uncensored", "https://javdb.com/actors/Ef34Gh"),
    ]
    monkeypatch.setattr("herald.cli.search_actors", lambda *_args, **_kwargs: hits)
    assert main(["feed", "search", "示例演员"]) == 0
    output = capsys.readouterr().out
    assert "Ab12Cd" in output
    assert "Ef34Gh" in output
    assert "does not choose" in output
    assert main(["feed", "list"]) == 0
    assert "no feed subscriptions" in capsys.readouterr().out
    assert main(["feed", "add", "2"]) == 0
    assert main(["feed", "list"]) == 0
    listed = capsys.readouterr().out
    assert "Ef34Gh" in listed
    assert "Ab12Cd" not in listed


def test_feed_add_by_actor_id(capsys):
    assert main(["feed", "add", "--javdb-actor", "Ab12Cd", "--name", "示例演员"]) == 0
    assert main(["feed", "add", "--javdb-actor", "Ab12Cd"]) == 0
    output = capsys.readouterr().out
    assert "added feed" in output
    assert "already subscribed" in output
    assert "https://javdb.com/actors/Ab12Cd" in output


def test_check_with_no_subscriptions_exits_clean(capsys):
    assert main(["check", "--once", "--quiet"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" not in captured.err


def test_loop_rejects_a_short_interval(capsys):
    assert main(["check", "--loop", "1"]) == 2
    assert "5 minutes" in capsys.readouterr().err


def test_config_telegram_and_partial_pair(capsys, monkeypatch):
    assert main(["config", "telegram", "--token", "123:abc", "--chat-id", "42"]) == 0
    assert "123:abc" not in capsys.readouterr().out
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # File still has the chat id, so the pair is complete via the file.
    assert load_settings().telegram_chat_id == "42"
    assert main(["config", "path"]) == 0
    assert "herald.db" in capsys.readouterr().out


def test_unknown_site_is_an_error(capsys):
    assert main(["follow", "add", "https://example.com/someone"]) == 2
    assert "unsupported" in capsys.readouterr().err


def test_stub_check_does_not_use_the_network(monkeypatch, capsys):
    def explode(*_args, **_kwargs):
        raise AssertionError("network client should not be built for this assertion path")

    monkeypatch.setattr(httpx.Client, "get", explode)
    assert main(["follow", "add", "https://simpcity.su/threads/example.1/"]) == 0
    capsys.readouterr()
    code = main(["check"])
    captured = capsys.readouterr()
    assert code == 1
    assert "SIMPCITY_COOKIE" in captured.err
    assert "bypass" in captured.err


def test_stub_with_cookie_still_does_not_fetch(monkeypatch, capsys):
    monkeypatch.setenv("SIMPCITY_COOKIE", "session=local")
    assert main(["follow", "add", "https://f95zone.to/threads/game.123/"]) == 0
    monkeypatch.setenv("F95_COOKIE", "xf_session=local")
    code = main(["check"])
    captured = capsys.readouterr()
    assert code == 1
    assert "not implemented" in captured.err
    assert "anti-bot" in captured.err
    assert "xf_session" not in captured.err
    assert "session=local" not in captured.err
