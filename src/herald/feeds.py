"""Subscribe to a JavDB actor without fetching the actor page."""

from __future__ import annotations

from herald.config import Settings
from herald.db import Database
from herald.models import Subscription
from herald.routing import parse_actor_ref
from herald.util import utc_now


def add_javdb_feed(
    db: Database,
    settings: Settings,
    actor_id: str,
    title: str | None,
) -> tuple[Subscription, bool]:
    actor_id, url = parse_actor_ref(actor_id, settings.javdb_base)
    return db.add_subscription(
        kind="feed",
        provider="javdb",
        target=actor_id,
        url=url,
        title=title,
        now=utc_now(),
    )
