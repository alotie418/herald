from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FoundItem:
    key: str
    title: str
    url: str
    published_at: str | None = None
    cover_url: str | None = None


@dataclass(frozen=True)
class FetchResult:
    items: list[FoundItem]
    display_name: str | None = None
    canonical_url: str | None = None


@dataclass(frozen=True)
class Subscription:
    id: int
    kind: str
    provider: str
    target: str
    url: str
    title: str | None
    enabled: bool
    baselined: bool
    created_at: str
    last_checked_at: str | None
    last_error: str | None

    @property
    def label(self) -> str:
        return self.title or self.target


@dataclass(frozen=True)
class PendingNotice:
    seen_id: int
    subscription_id: int
    kind: str
    provider: str
    target: str
    sub_title: str | None
    item_key: str
    item_title: str
    item_url: str
    cover_url: str | None = None

    @property
    def label(self) -> str:
        return self.sub_title or self.target


@dataclass(frozen=True)
class ActorHit:
    actor_id: str
    name: str
    aliases: str
    marker: str
    url: str
