"""Credentialed sites that Herald deliberately does not fetch.

SimpCity, rule34.xxx, and F95zone can be stored as subscriptions and can keep
a cookie or API key in the private config. Check reports that the fetch is not
implemented. Nothing in this module sends those secrets or solves a challenge.
"""

from __future__ import annotations

from herald.config import Settings
from herald.errors import AuthRequired, ProviderNotImplemented
from herald.http import HttpClient
from herald.models import FetchResult, Subscription


class StubProvider:
    def __init__(self, name: str, env_vars: tuple[str, ...], hint: str) -> None:
        self.name = name
        self.env_vars = env_vars
        self.hint = hint

    def fetch(self, sub: Subscription, client: HttpClient, settings: Settings) -> FetchResult:
        del client, sub
        missing = [name for name in self.env_vars if not settings.secrets.get(name)]
        if missing:
            joined = ", ".join(missing)
            raise AuthRequired(
                f"{self.name} requires {self.hint} ({joined}). "
                "Set it with `herald config set` or the environment. "
                "Herald does not bypass a login wall."
            )
        raise ProviderNotImplemented(
            f"{self.name} has credentials configured, but fetching is not implemented. "
            "Herald will not start a cookie or API session that depends on getting past anti-bot checks."
        )
