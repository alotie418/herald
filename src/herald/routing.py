"""Turn a public profile URL into a provider and a safe target id.

Request URLs are rebuilt from these pieces. The host allow-list is the
boundary: Herald does not fetch an arbitrary address the user pasted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from herald.config import validate_base
from herald.errors import ConfigError

KEMONO_HOSTS = ("kemono.cr", "kemono.su", "kemono.party")
COOMER_HOSTS = ("coomer.st", "coomer.su", "coomer.party")
SIMPCITY_HOSTS = ("simpcity.su", "simpcity.cr", "simpcity.com")
RULE34_HOSTS = ("rule34.xxx",)
F95_HOSTS = ("f95zone.to", "f95zone.com")

PATREON_RESERVED = {
    "about",
    "api",
    "bepatron",
    "c",
    "checkout",
    "create",
    "explore",
    "home",
    "login",
    "oauth2",
    "policy",
    "posts",
    "privacy",
    "rss",
    "search",
    "settings",
    "signup",
    "user",
}
SUBSCRIBESTAR_RESERVED = {
    "about",
    "account",
    "explore",
    "feed",
    "help",
    "login",
    "posts",
    "privacy",
    "signup",
    "support",
}
ACTOR_RESERVED = {"censored", "list", "ranking", "search", "tags", "uncensored"}

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
_ACTOR = re.compile(r"[A-Za-z0-9]{2,32}")


@dataclass(frozen=True)
class ParsedFollow:
    provider: str
    target: str
    url: str


def require_segment(value: str, what: str) -> str:
    if not _SEGMENT.fullmatch(value) or ".." in value:
        raise ConfigError(f"unsupported {what}: {value!r}")
    return value


def require_actor_id(value: str) -> str:
    if not _ACTOR.fullmatch(value) or value.lower() in ACTOR_RESERVED:
        raise ConfigError(f"invalid JavDB actor id: {value!r}")
    return value


def _split(raw: str) -> tuple[SplitResult, str]:
    text = raw.strip()
    if not text or any(char in text for char in "\r\n\t "):
        raise ConfigError("URL is empty or contains whitespace")
    parts = urlsplit(text)
    if parts.username or parts.password:
        raise ConfigError("URL must not contain credentials")
    if parts.scheme != "https":
        raise ConfigError("URL must start with https://")
    if parts.port is not None:
        raise ConfigError("URL must not include a port")
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        bare = host[4:]
    else:
        bare = host
    if not bare or ".." in parts.path:
        raise ConfigError("URL is not a supported profile link")
    return parts, bare


def parse_follow_url(raw: str) -> ParsedFollow:
    parts, bare = _split(raw)
    if bare in KEMONO_HOSTS:
        return _archive("kemono", bare, parts)
    if bare in COOMER_HOSTS:
        return _archive("coomer", bare, parts)
    if bare == "fanbox.cc" or bare.endswith(".fanbox.cc"):
        return _fanbox(bare, parts)
    if bare == "patreon.com":
        return _patreon(parts)
    if bare in {"subscribestar.com", "subscribestar.adult"}:
        return _subscribestar(bare, parts)
    if bare in SIMPCITY_HOSTS:
        return _stub("simpcity", bare, parts)
    if bare in RULE34_HOSTS:
        return _stub("rule34", bare, parts)
    if bare in F95_HOSTS:
        return _stub("f95", bare, parts)
    if "javdb" in bare.split(".")[0]:
        raise ConfigError("JavDB actor pages are feeds. Use: herald feed add URL")
    raise ConfigError(
        "unsupported site. Public MVP: Kemono, Coomer, FANBOX, Patreon, and "
        "SubscribeStar public pages. Credential stubs (no fetch): SimpCity, "
        "rule34.xxx, F95zone. JavDB actors use `herald feed`."
    )


def parse_actor_ref(raw: str, base: str) -> tuple[str, str]:
    """Return ``(actor_id, canonical_url)`` on the configured JavDB origin."""
    origin = validate_base(base)
    text = raw.strip()
    if "://" not in text:
        actor_id = require_actor_id(text)
        return actor_id, f"{origin}/actors/{actor_id}"
    parts, _bare = _split(text)
    pieces = [piece for piece in parts.path.split("/") if piece]
    if len(pieces) < 2 or pieces[0].lower() != "actors":
        raise ConfigError("JavDB actor URL must look like https://javdb.com/actors/ID")
    actor_id = require_actor_id(pieces[1])
    return actor_id, f"{origin}/actors/{actor_id}"


def _archive(provider: str, bare: str, parts) -> ParsedFollow:
    pieces = [piece for piece in parts.path.split("/") if piece]
    if len(pieces) < 3 or pieces[1].lower() != "user":
        example = pieces[0] if pieces else "service"
        raise ConfigError(f"{bare} links must look like https://{bare}/{example}/user/ID")
    service = require_segment(pieces[0].lower(), "service")
    user = require_segment(pieces[2], "creator id")
    if service == "user":
        raise ConfigError(f"{bare} links must look like https://{bare}/service/user/ID")
    return ParsedFollow(
        provider=provider,
        target=f"{service}:{user}@{bare}",
        url=f"https://{bare}/{service}/user/{user}",
    )


def _fanbox(bare: str, parts) -> ParsedFollow:
    if bare == "fanbox.cc":
        pieces = [piece for piece in parts.path.split("/") if piece]
        if not pieces or not pieces[0].startswith("@"):
            raise ConfigError("FANBOX links must look like https://www.fanbox.cc/@creator")
        creator = require_segment(pieces[0][1:], "FANBOX creator")
    else:
        creator = require_segment(bare[: -len(".fanbox.cc")], "FANBOX creator")
        if creator in {"api", "downloads", "www"}:
            raise ConfigError("that FANBOX host is not a creator page")
    return ParsedFollow("fanbox", creator, f"https://www.fanbox.cc/@{creator}")


def _patreon(parts) -> ParsedFollow:
    pieces = [piece for piece in parts.path.split("/") if piece]
    if not pieces:
        raise ConfigError("add a Patreon creator URL, not the homepage")
    if pieces[0].lower() == "cw":
        if len(pieces) < 2:
            raise ConfigError("Patreon /cw/ links need a creator name")
        vanity = require_segment(pieces[1], "Patreon creator").lower()
        return ParsedFollow("patreon", f"cw:{vanity}", f"https://www.patreon.com/cw/{vanity}")
    if pieces[0].lower() in PATREON_RESERVED:
        raise ConfigError("add the Patreon creator profile URL, not a single post or site page")
    vanity = require_segment(pieces[0], "Patreon creator").lower()
    return ParsedFollow("patreon", vanity, f"https://www.patreon.com/{vanity}")


def _subscribestar(bare: str, parts) -> ParsedFollow:
    pieces = [piece for piece in parts.path.split("/") if piece]
    if not pieces or pieces[0].lower() in SUBSCRIBESTAR_RESERVED:
        raise ConfigError("add a SubscribeStar creator URL, not the homepage")
    name = require_segment(pieces[0], "SubscribeStar creator").lower()
    site = "adult" if bare == "subscribestar.adult" else "main"
    host = "subscribestar.adult" if site == "adult" else "www.subscribestar.com"
    return ParsedFollow("subscribestar", f"{site}:{name}", f"https://{host}/{name}")


def _stub(provider: str, bare: str, parts) -> ParsedFollow:
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    query = f"?{parts.query}" if parts.query else ""
    url = f"https://{bare}{path}{query}"
    if len(url) > 300:
        raise ConfigError("URL is too long")
    return ParsedFollow(provider, url, url)
