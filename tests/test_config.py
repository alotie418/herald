import pytest

from herald.config import load_settings, set_values
from herald.errors import ConfigError
from herald.http import make_client
from herald.secure import mode_bits


def test_config_file_is_private_and_round_trips(isolated):
    path = isolated / "config.toml"
    set_values({"telegram.token": "123:abc", "telegram.chat_id": "-1009"})
    assert mode_bits(path) == 0o600
    assert mode_bits(path.parent) == 0o700
    settings = load_settings()
    assert settings.telegram_token == "123:abc"
    assert settings.telegram_chat_id == "-1009"


def test_environment_overrides_file_and_show_redacts(isolated, capsys, monkeypatch):
    from herald.cli import main

    set_values({"telegram.token": "111:file-token", "telegram.chat_id": "5"})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "222:env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "77")
    assert load_settings().telegram_token == "222:env-token"
    assert main(["config", "show"]) == 0
    output = capsys.readouterr().out
    assert "111:file-token" not in output
    assert "222:env-token" not in output
    assert "77" in output
    assert "set via environment" in output


def test_delay_below_half_a_second_is_rejected():
    with pytest.raises(ConfigError):
        set_values({"http.min_delay_seconds": "0.1"})


def test_javdb_floor_stays_at_five_seconds(isolated):
    set_values(
        {
            "http.min_delay_seconds": "0.5",
            "http.javdb_base": "https://javdb521.com",
        }
    )
    client = make_client(load_settings(), trust_env=False)
    try:
        assert client.limiter.interval_for("javdb521.com") >= 5
        assert client.limiter.interval_for("javdb.com") >= 5
        assert client.limiter.interval_for("kemono.cr") == 0.5
    finally:
        client.close()


def test_database_file_is_private(isolated):
    from herald.db import Database

    path = isolated / "data" / "herald.db"
    with Database(path):
        pass
    assert mode_bits(path) == 0o600
    assert mode_bits(path.parent) == 0o700
