"""Poll subscriptions, remember what was already seen, and queue notices."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from herald.config import Settings
from herald.db import Database
from herald.errors import CoverUnavailable, HeraldError
from herald.http import HttpClient
from herald.models import FoundItem, PendingNotice, Subscription
from herald.notify import TelegramNotifier, render_caption, render_messages
from herald.providers.base import Provider
from herald.providers.registry import get_provider
from herald.util import clip, utc_now

Progress = Callable[[Subscription], None]


@dataclass
class CheckReport:
    baselines: list[tuple[int, str, int]] = field(default_factory=list)
    fresh: list[tuple[int, str, FoundItem]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notified: int = 0
    telegram_missing: bool = False
    telegram_error: str | None = None
    dry_run: bool = False

    @property
    def exit_code(self) -> int:
        if self.dry_run:
            return 1 if self.errors else 0
        if self.telegram_missing or self.telegram_error:
            return 2
        if self.errors:
            return 1
        return 0


def run_check(
    settings: Settings,
    db: Database,
    client: HttpClient,
    *,
    notifier: TelegramNotifier | None,
    dry_run: bool = False,
    notify_existing: bool = False,
    providers: Mapping[str, Provider] | None = None,
    on_progress: Progress | None = None,
    now: str | None = None,
) -> CheckReport:
    stamp = now or utc_now()
    report = CheckReport(dry_run=dry_run)
    for sub in db.list_subscriptions(enabled_only=True):
        if on_progress is not None:
            on_progress(sub)
        try:
            provider = _provider(sub.provider, providers)
            fetched = provider.fetch(sub, client, settings)
        except HeraldError as exc:
            _remember_error(db, sub, exc, dry_run, stamp, report)
            continue
        except Exception as exc:
            _remember_error(db, sub, exc, dry_run, stamp, report)
            continue
        if not dry_run:
            db.mark_checked(sub.id, error=None, now=stamp)
            if fetched.display_name and not sub.title:
                db.set_title(sub.id, fetched.display_name)
            if fetched.canonical_url and fetched.canonical_url != sub.url:
                db.set_url(sub.id, fetched.canonical_url)
        fresh = [item for item in fetched.items if not db.has_seen(sub.id, item.key)]
        label = fetched.display_name or sub.label
        if dry_run:
            if not sub.baselined and not notify_existing:
                report.baselines.append((sub.id, label, len(fetched.items)))
            else:
                report.fresh.extend((sub.id, label, item) for item in fresh)
            continue
        if not sub.baselined and not notify_existing:
            for item in fetched.items:
                db.add_seen(sub.id, item, notify=False, now=stamp)
            db.set_baselined(sub.id)
            report.baselines.append((sub.id, label, len(fetched.items)))
            continue
        for item in fresh:
            db.add_seen(sub.id, item, notify=True, now=stamp)
        if not sub.baselined:
            db.set_baselined(sub.id)
        report.fresh.extend((sub.id, label, item) for item in fresh)

    if dry_run:
        return report
    pending = db.pending_notices()
    if not pending:
        return report
    if notifier is None:
        report.telegram_missing = True
        return report
    try:
        _deliver(notifier, pending, db, stamp, report)
    except HeraldError as exc:
        report.telegram_error = str(exc)
    return report


def _deliver(
    notifier: TelegramNotifier,
    pending: list[PendingNotice],
    db: Database,
    stamp: str,
    report: CheckReport,
) -> None:
    text_batch: list[PendingNotice] = []
    photo_batch: list[tuple[PendingNotice, bytes, str]] = []

    def flush_text() -> None:
        nonlocal text_batch
        for text, seen_ids in render_messages(text_batch):
            notifier.send(text)
            db.mark_notified(seen_ids, now=stamp)
            report.notified += len(seen_ids)
        text_batch = []

    def flush_photos() -> None:
        nonlocal photo_batch
        if photo_batch:
            _send_photo_batch(notifier, photo_batch, db, stamp, report)
        photo_batch = []

    for item in pending:
        if not item.cover_url:
            flush_photos()
            text_batch.append(item)
            continue
        try:
            data, mime = notifier.fetch_cover(item.cover_url)
        except CoverUnavailable:
            flush_photos()
            text_batch.append(item)
            continue
        flush_text()
        photo_batch.append((item, data, mime))
        if len(photo_batch) == 10:
            flush_photos()
    flush_photos()
    flush_text()


def _send_photo_batch(
    notifier: TelegramNotifier,
    batch: list[tuple[PendingNotice, bytes, str]],
    db: Database,
    stamp: str,
    report: CheckReport,
) -> None:
    payloads = [(render_caption(notice), data, mime) for notice, data, mime in batch]
    try:
        if len(payloads) == 1:
            notifier.send_photo_bytes(*payloads[0])
        else:
            notifier.send_media_group(payloads)
    except CoverUnavailable:
        for notice, data, mime in batch:
            _send_one_photo_or_text(notifier, notice, data, mime, db, stamp, report)
        return
    seen_ids = [notice.seen_id for notice, _data, _mime in batch]
    db.mark_notified(seen_ids, now=stamp)
    report.notified += len(seen_ids)


def _send_one_photo_or_text(
    notifier: TelegramNotifier,
    notice: PendingNotice,
    data: bytes,
    mime: str,
    db: Database,
    stamp: str,
    report: CheckReport,
) -> None:
    try:
        notifier.send_photo_bytes(render_caption(notice), data, mime)
    except CoverUnavailable:
        text, _ids = render_messages([notice])[0]
        notifier.send(text)
    db.mark_notified([notice.seen_id], now=stamp)
    report.notified += 1


def format_report(report: CheckReport) -> str:
    lines: list[str] = []
    prefix = "dry-run " if report.dry_run else ""
    for sub_id, label, count in report.baselines:
        lines.append(
            f"{prefix}baseline  #{sub_id} {label}: recorded {count} existing items, no notification"
        )
    for sub_id, label, item in report.fresh:
        lines.append(f"{prefix}new       #{sub_id} {label}: {item.title}")
        lines.append(f"          {item.url}")
    if report.notified:
        lines.append(f"notified  {report.notified} via Telegram")
    if report.dry_run and report.fresh:
        lines.append("dry-run   Telegram was not called")
    if report.telegram_missing:
        lines.append("pending   Telegram is not configured; items stay queued")
    if report.telegram_error:
        lines.append("pending   Telegram send failed; items stay queued")
    if not lines:
        lines.append("no new items")
    lines.append(
        f"summary   baselines={len(report.baselines)} new={len(report.fresh)} errors={len(report.errors)}"
    )
    return "\n".join(lines)


def quiet_lines(report: CheckReport) -> str:
    lines: list[str] = []
    for _sub_id, label, item in report.fresh:
        lines.append(f"{label}: {item.title}")
        lines.append(item.url)
    if report.telegram_missing:
        lines.append("pending: Telegram is not configured")
    if report.telegram_error:
        lines.append("pending: Telegram send failed")
    return "\n".join(lines)


def _provider(name: str, providers: Mapping[str, Provider] | None) -> Provider:
    if providers is None:
        return get_provider(name)
    try:
        return providers[name]
    except KeyError as exc:
        raise HeraldError(f"unknown provider {name}") from exc


def _remember_error(
    db: Database,
    sub: Subscription,
    exc: Exception,
    dry_run: bool,
    stamp: str,
    report: CheckReport,
) -> None:
    message = clip(str(exc), 400)
    report.errors.append(f"#{sub.id} {sub.provider}: {message}")
    if not dry_run:
        db.mark_checked(sub.id, error=message, now=stamp)
