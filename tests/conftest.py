import pytest

from herald.models import Subscription


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HERALD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HERALD_CONFIG", str(tmp_path / "config.toml"))
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "JAVDB_COOKIE",
        "JAVDB_BASE",
        "SIMPCITY_COOKIE",
        "RULE34_USER_ID",
        "RULE34_API_KEY",
        "F95_COOKIE",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def subscription(**overrides) -> Subscription:
    fields = {
        "id": 1,
        "kind": "follow",
        "provider": "kemono",
        "target": "patreon:42@kemono.cr",
        "url": "https://kemono.cr/patreon/user/42",
        "title": None,
        "enabled": True,
        "baselined": False,
        "created_at": "2026-01-01T00:00:00Z",
        "last_checked_at": None,
        "last_error": None,
    }
    fields.update(overrides)
    return Subscription(**fields)
