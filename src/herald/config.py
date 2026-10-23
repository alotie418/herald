"""Resolve settings from the environment and a private TOML file.

Environment variables win over the file. Secrets are never printed by the
commands in this module's helpers.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from herald.errors import ConfigError
from herald.secure import mode_bits, write_private

ENV_TOKEN = "TELEGRAM_BOT_TOKEN"
ENV_CHAT = "TELEGRAM_CHAT_ID"
ENV_CONFIG = "HERALD_CONFIG"
ENV_DATA = "HERALD_DATA_DIR"

SECRET_ENV = {
    "SIMPCITY_COOKIE": "simpcity_cookie",
    "RULE34_USER_ID": "rule34_user_id",
    "RULE34_API_KEY": "rule34_api_key",
    "F95_COOKIE": "f95_cookie",
    "JAVDB_COOKIE": "javdb_cookie",
}

SET_KEYS = {
    "telegram.token": "telegram_token",
    "telegram.chat_id": "telegram_chat_id",
    "http.min_delay_seconds": "min_delay_seconds",
    "http.timeout_seconds": "timeout_seconds",
    "http.javdb_base": "javdb_base",
    "credentials.simpcity_cookie": "simpcity_cookie",
    "credentials.rule34_user_id": "rule34_user_id",
    "credentials.rule34_api_key": "rule34_api_key",
    "credentials.f95_cookie": "f95_cookie",
    "credentials.javdb_cookie": "javdb_cookie",
    "SIMPCITY_COOKIE": "simpcity_cookie",
    "RULE34_USER_ID": "rule34_user_id",
    "RULE34_API_KEY": "rule34_api_key",
    "F95_COOKIE": "f95_cookie",
    "JAVDB_COOKIE": "javdb_cookie",
    "JAVDB_BASE": "javdb_base",
}

REDACTED_FIELDS = {
    "telegram_token",
    "simpcity_cookie",
    "rule34_api_key",
    "f95_cookie",
    "javdb_cookie",
}


@dataclass
class FileConfig:
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    min_delay_seconds: float = 2.0
    timeout_seconds: float = 30.0
    javdb_base: str = "https://javdb.com"
    simpcity_cookie: str | None = None
    rule34_user_id: str | None = None
    rule34_api_key: str | None = None
    f95_cookie: str | None = None
    javdb_cookie: str | None = None


@dataclass(frozen=True)
class Settings:
    config_path: Path
    data_dir: Path
    telegram_token: str | None
    telegram_chat_id: str | None
    min_delay_seconds: float
    timeout_seconds: float
    javdb_base: str
    secrets: dict[str, str]
    user_agent: str


def config_path() -> Path:
    override = os.environ.get(ENV_CONFIG)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "herald" / "config.toml"


def data_dir() -> Path:
    override = os.environ.get(ENV_DATA)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "herald"


def validate_base(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ConfigError("javdb_base must be an https origin without credentials")
    if parts.port is not None:
        raise ConfigError("javdb_base must not include a port")
    if parts.path not in ("", "/"):
        raise ConfigError("javdb_base must be an origin, not a page path")
    return f"https://{parts.hostname}"


def _blank_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: object, default: float, field: str) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be a number") from exc


def load_file(path: Path | None = None) -> FileConfig:
    path = path or config_path()
    if not path.exists():
        return FileConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a TOML table")
    telegram = data.get("telegram") or {}
    http = data.get("http") or {}
    credentials = data.get("credentials") or {}
    if not all(isinstance(part, dict) for part in (telegram, http, credentials)):
        raise ConfigError(f"{path} has a section that is not a table")
    cfg = FileConfig(
        telegram_token=_blank_to_none(telegram.get("token")),
        telegram_chat_id=_blank_to_none(telegram.get("chat_id")),
        min_delay_seconds=_as_float(http.get("min_delay_seconds"), 2.0, "http.min_delay_seconds"),
        timeout_seconds=_as_float(http.get("timeout_seconds"), 30.0, "http.timeout_seconds"),
        javdb_base=str(http.get("javdb_base") or "https://javdb.com"),
        simpcity_cookie=_blank_to_none(credentials.get("simpcity_cookie")),
        rule34_user_id=_blank_to_none(credentials.get("rule34_user_id")),
        rule34_api_key=_blank_to_none(credentials.get("rule34_api_key")),
        f95_cookie=_blank_to_none(credentials.get("f95_cookie")),
        javdb_cookie=_blank_to_none(credentials.get("javdb_cookie")),
    )
    _validate_file(cfg)
    return cfg


def _validate_file(cfg: FileConfig) -> None:
    if cfg.min_delay_seconds < 0.5 or cfg.min_delay_seconds > 3600:
        raise ConfigError("http.min_delay_seconds must be between 0.5 and 3600")
    if cfg.timeout_seconds < 5 or cfg.timeout_seconds > 120:
        raise ConfigError("http.timeout_seconds must be between 5 and 120")
    cfg.javdb_base = validate_base(cfg.javdb_base)
    if cfg.telegram_token is not None:
        cfg.telegram_token = _validate_token(cfg.telegram_token)
    if cfg.telegram_chat_id is not None:
        cfg.telegram_chat_id = _validate_chat_id(cfg.telegram_chat_id)
    for field in REDACTED_FIELDS | {"rule34_user_id"}:
        value = getattr(cfg, field)
        if value is not None:
            _validate_secret(value, field)


def _validate_token(token: str) -> str:
    if any(char.isspace() for char in token) or len(token) > 200:
        raise ConfigError("Telegram token must be a single token without spaces")
    return token


def _validate_chat_id(chat_id: str) -> str:
    if not chat_id or chat_id[0] == "-" and not chat_id[1:].isdigit():
        raise ConfigError("Telegram chat id must be an integer, optionally negative")
    if chat_id[0] != "-" and not chat_id.isdigit():
        raise ConfigError("Telegram chat id must be an integer, optionally negative")
    if len(chat_id) > 32:
        raise ConfigError("Telegram chat id is too long")
    return chat_id


def _validate_secret(value: str, field: str) -> None:
    if any(char in value for char in "\r\n") or len(value) > 4096:
        raise ConfigError(f"{field} must be a single line")


def load_settings() -> Settings:
    file_cfg = load_file()
    token = _blank_to_none(os.environ.get(ENV_TOKEN)) or file_cfg.telegram_token
    chat_id = _blank_to_none(os.environ.get(ENV_CHAT)) or file_cfg.telegram_chat_id
    if token is not None:
        token = _validate_token(token)
    if chat_id is not None:
        chat_id = _validate_chat_id(chat_id)
    base = _blank_to_none(os.environ.get("JAVDB_BASE")) or file_cfg.javdb_base
    base = validate_base(base)
    secrets: dict[str, str] = {}
    for env_name, attr in SECRET_ENV.items():
        value = _blank_to_none(os.environ.get(env_name)) or getattr(file_cfg, attr)
        if value:
            _validate_secret(value, env_name)
            secrets[env_name] = value
    from herald import __version__

    return Settings(
        config_path=config_path(),
        data_dir=data_dir(),
        telegram_token=token,
        telegram_chat_id=chat_id,
        min_delay_seconds=file_cfg.min_delay_seconds,
        timeout_seconds=file_cfg.timeout_seconds,
        javdb_base=base,
        secrets=secrets,
        user_agent=f"Herald/{__version__} (+local follow checker; rate-limited; no media download)",
    )


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def dump_file(cfg: FileConfig) -> str:
    lines = [
        "# Herald configuration. May contain secrets. Keep this file mode 0600.",
        "[telegram]",
        f"token = {_toml_string(cfg.telegram_token or '')}",
        f"chat_id = {_toml_string(cfg.telegram_chat_id or '')}",
        "",
        "[http]",
        f"min_delay_seconds = {cfg.min_delay_seconds}",
        f"timeout_seconds = {cfg.timeout_seconds}",
        f"javdb_base = {_toml_string(cfg.javdb_base)}",
        "",
        "[credentials]",
        f"simpcity_cookie = {_toml_string(cfg.simpcity_cookie or '')}",
        f"rule34_user_id = {_toml_string(cfg.rule34_user_id or '')}",
        f"rule34_api_key = {_toml_string(cfg.rule34_api_key or '')}",
        f"f95_cookie = {_toml_string(cfg.f95_cookie or '')}",
        f"javdb_cookie = {_toml_string(cfg.javdb_cookie or '')}",
        "",
    ]
    return "\n".join(lines)


def save_file(cfg: FileConfig, path: Path | None = None) -> None:
    _validate_file(cfg)
    write_private(path or config_path(), dump_file(cfg))


def set_values(updates: dict[str, str], path: Path | None = None) -> FileConfig:
    cfg = load_file(path)
    if not updates:
        raise ConfigError("no settings to update")
    for key, raw in updates.items():
        attr = SET_KEYS.get(key)
        if attr is None:
            known = ", ".join(sorted(k for k in SET_KEYS if "." in k))
            raise ConfigError(f"unknown setting {key!r}. Known keys: {known}")
        value = raw.strip()
        if not value:
            raise ConfigError(f"{key} cannot be empty")
        if attr in {"min_delay_seconds", "timeout_seconds"}:
            setattr(cfg, attr, _as_float(value, 0, key))
        else:
            setattr(cfg, attr, value)
    _validate_file(cfg)
    save_file(cfg, path)
    return cfg


def loose_permissions_warning(path: Path) -> str | None:
    if not path.exists():
        return None
    mode = mode_bits(path)
    if mode & 0o077:
        return f"warning: {path} is mode {mode:04o}; expected 0600. Run chmod 600 on that file."
    return None


def secret_source(env_name: str, file_value: str | None) -> str:
    if os.environ.get(env_name):
        return "set via environment"
    if file_value:
        return "set in config file"
    return "unset"
