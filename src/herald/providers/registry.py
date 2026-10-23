from __future__ import annotations

from herald.errors import HeraldError
from herald.providers.archive import ArchiveProvider
from herald.providers.base import Provider
from herald.providers.fanbox import FanboxProvider
from herald.providers.javdb import JavdbProvider
from herald.providers.publicpage import PublicPageProvider
from herald.providers.stubs import StubProvider
from herald.routing import COOMER_HOSTS, KEMONO_HOSTS

PROVIDERS: dict[str, Provider] = {
    "kemono": ArchiveProvider("kemono", KEMONO_HOSTS),
    "coomer": ArchiveProvider("coomer", COOMER_HOSTS),
    "fanbox": FanboxProvider(),
    "patreon": PublicPageProvider("patreon"),
    "subscribestar": PublicPageProvider("subscribestar"),
    "javdb": JavdbProvider(),
    "simpcity": StubProvider("simpcity", ("SIMPCITY_COOKIE",), "a browser cookie"),
    "rule34": StubProvider(
        "rule34",
        ("RULE34_USER_ID", "RULE34_API_KEY"),
        "a user id and API key",
    ),
    "f95": StubProvider("f95", ("F95_COOKIE",), "a browser cookie"),
}


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise HeraldError(f"unknown provider {name}") from exc
