from __future__ import annotations

from typing import Protocol

from herald.config import Settings
from herald.http import HttpClient
from herald.models import FetchResult, Subscription


class Provider(Protocol):
    name: str

    def fetch(self, sub: Subscription, client: HttpClient, settings: Settings) -> FetchResult:
        """Return the current public items for one subscription."""
