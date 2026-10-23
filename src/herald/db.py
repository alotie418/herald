"""SQLite storage for subscriptions, seen items, and the last actor search."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from herald.models import ActorHit, FoundItem, PendingNotice, Subscription
from herald.secure import harden_dir, harden_file

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('follow', 'feed')),
    provider TEXT NOT NULL,
    target TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    baselined INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_error TEXT,
    UNIQUE (kind, provider, target)
);

CREATE TABLE IF NOT EXISTS seen_items (
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    title TEXT,
    url TEXT,
    published_at TEXT,
    cover_url TEXT,
    first_seen_at TEXT NOT NULL,
    notified_at TEXT,
    UNIQUE (subscription_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_seen_pending
    ON seen_items (notified_at, id);

CREATE TABLE IF NOT EXISTS search_hits (
    position INTEGER PRIMARY KEY,
    actor_id TEXT NOT NULL,
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '',
    marker TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    searched_at TEXT NOT NULL
);
"""


def _subscription(row: sqlite3.Row) -> Subscription:
    return Subscription(
        id=row["id"],
        kind=row["kind"],
        provider=row["provider"],
        target=row["target"],
        url=row["url"],
        title=row["title"],
        enabled=bool(row["enabled"]),
        baselined=bool(row["baselined"]),
        created_at=row["created_at"],
        last_checked_at=row["last_checked_at"],
        last_error=row["last_error"],
    )


class Database:
    def __init__(self, path: Path) -> None:
        harden_dir(path.parent)
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()
        harden_file(path)

    def _migrate(self) -> None:
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(seen_items)")}
        if "cover_url" not in columns:
            self._conn.execute("ALTER TABLE seen_items ADD COLUMN cover_url TEXT")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def add_subscription(
        self,
        *,
        kind: str,
        provider: str,
        target: str,
        url: str,
        title: str | None,
        now: str,
    ) -> tuple[Subscription, bool]:
        existing = self.find(kind, provider, target)
        if existing is not None:
            if title and title != existing.title:
                self.set_title(existing.id, title)
                existing = self.get(existing.id)
            assert existing is not None
            return existing, False
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO subscriptions (kind, provider, target, url, title, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (kind, provider, target, url, title, now),
            )
        created = self.get(int(cursor.lastrowid))
        assert created is not None
        return created, True

    def get(self, sub_id: int) -> Subscription | None:
        row = self._conn.execute(
            "SELECT * FROM subscriptions WHERE id = ?", (sub_id,)
        ).fetchone()
        return _subscription(row) if row else None

    def find(self, kind: str, provider: str, target: str) -> Subscription | None:
        row = self._conn.execute(
            """
            SELECT * FROM subscriptions
            WHERE kind = ? AND provider = ? AND target = ?
            """,
            (kind, provider, target),
        ).fetchone()
        return _subscription(row) if row else None

    def list_subscriptions(self, *, enabled_only: bool = False) -> list[Subscription]:
        sql = "SELECT * FROM subscriptions"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY kind, id"
        return [_subscription(row) for row in self._conn.execute(sql)]

    def delete(self, sub_id: int) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))

    def set_enabled(self, sub_id: int, enabled: bool) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE subscriptions SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, sub_id),
            )

    def set_title(self, sub_id: int, title: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE subscriptions SET title = ? WHERE id = ?", (title, sub_id)
            )

    def set_url(self, sub_id: int, url: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE subscriptions SET url = ? WHERE id = ?", (url, sub_id)
            )

    def set_baselined(self, sub_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE subscriptions SET baselined = 1 WHERE id = ?", (sub_id,)
            )

    def mark_checked(self, sub_id: int, *, error: str | None, now: str) -> None:
        with self._conn:
            self._conn.execute(
                """
                UPDATE subscriptions
                SET last_checked_at = ?, last_error = ?
                WHERE id = ?
                """,
                (now, error, sub_id),
            )

    def has_seen(self, sub_id: int, key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_items WHERE subscription_id = ? AND item_key = ?",
            (sub_id, key),
        ).fetchone()
        return row is not None

    def add_seen(self, sub_id: int, item: FoundItem, *, notify: bool, now: str) -> bool:
        notified_at = None if notify else now
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO seen_items (
                    subscription_id, item_key, title, url, published_at, cover_url,
                    first_seen_at, notified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subscription_id, item_key) DO NOTHING
                """,
                (
                    sub_id,
                    item.key,
                    item.title,
                    item.url,
                    item.published_at,
                    item.cover_url,
                    now,
                    notified_at,
                ),
            )
        return cursor.rowcount == 1

    def pending_notices(self) -> list[PendingNotice]:
        rows = self._conn.execute(
            """
            SELECT
                i.id AS seen_id,
                i.subscription_id,
                i.item_key,
                i.title AS item_title,
                i.url AS item_url,
                i.cover_url AS cover_url,
                s.kind,
                s.provider,
                s.target,
                s.title AS sub_title
            FROM seen_items i
            JOIN subscriptions s ON s.id = i.subscription_id
            WHERE i.notified_at IS NULL
            ORDER BY i.id
            """
        )
        return [
            PendingNotice(
                seen_id=row["seen_id"],
                subscription_id=row["subscription_id"],
                kind=row["kind"],
                provider=row["provider"],
                target=row["target"],
                sub_title=row["sub_title"],
                item_key=row["item_key"],
                item_title=row["item_title"] or "",
                item_url=row["item_url"] or "",
                cover_url=row["cover_url"] or None,
            )
            for row in rows
        ]

    def mark_notified(self, seen_ids: list[int], *, now: str) -> None:
        if not seen_ids:
            return
        with self._conn:
            self._conn.executemany(
                """
                UPDATE seen_items
                SET notified_at = ?
                WHERE id = ? AND notified_at IS NULL
                """,
                [(now, seen_id) for seen_id in seen_ids],
            )

    def replace_search(self, hits: list[ActorHit], now: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM search_hits")
            self._conn.executemany(
                """
                INSERT INTO search_hits (
                    position, actor_id, name, aliases, marker, url, searched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (index, hit.actor_id, hit.name, hit.aliases, hit.marker, hit.url, now)
                    for index, hit in enumerate(hits, start=1)
                ],
            )

    def get_search_hit(self, position: int) -> ActorHit | None:
        row = self._conn.execute(
            "SELECT * FROM search_hits WHERE position = ?", (position,)
        ).fetchone()
        if row is None:
            return None
        return ActorHit(
            actor_id=row["actor_id"],
            name=row["name"],
            aliases=row["aliases"],
            marker=row["marker"],
            url=row["url"],
        )
