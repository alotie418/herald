"""Herald command line."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence

from herald import __version__
from herald.check import format_report, quiet_lines, run_check
from herald.config import (
    SET_KEYS,
    config_path,
    data_dir,
    load_file,
    load_settings,
    loose_permissions_warning,
    secret_source,
    set_values,
)
from herald.db import Database
from herald.errors import AlreadyRunning, ConfigError, HeraldError
from herald.http import make_client
from herald.lockfile import file_lock
from herald.notify import TelegramNotifier
from herald.providers.javdb import search_actors
from herald.routing import parse_actor_ref, parse_follow_url
from herald.util import collapse, utc_now

MARKERS = {"": "", "censored": "有码", "uncensored": "无码"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herald",
        description=(
            "Watch creator posts and JavDB actress releases, then notify Telegram. "
            "Does not download media."
        ),
    )
    parser.add_argument("--version", action="version", version=f"herald {__version__}")
    parser.add_argument("--data-dir", help="override the data directory (or set HERALD_DATA_DIR)")
    parser.add_argument("--config", help="override the config file (or set HERALD_CONFIG)")
    sub = parser.add_subparsers(dest="command", required=True)

    follow = sub.add_parser("follow", help="creator profiles")
    follow_sub = follow.add_subparsers(dest="follow_cmd", required=True)
    add = follow_sub.add_parser("add", help="remember a creator URL (does not fetch yet)")
    add.add_argument("url")
    add.add_argument("--title", help="display name")
    add.set_defaults(func=cmd_follow_add)
    follow_sub.add_parser("list", help="list creator subscriptions").set_defaults(func=cmd_follow_list)
    remove = follow_sub.add_parser("remove", help="delete a creator subscription and its seen items")
    remove.add_argument("ref", help="subscription id or profile URL")
    remove.set_defaults(func=cmd_follow_remove)
    pause = follow_sub.add_parser("pause", help="skip a creator during check")
    pause.add_argument("ref", help="subscription id or profile URL")
    pause.set_defaults(func=cmd_follow_pause)
    resume = follow_sub.add_parser("resume", help="include a creator during check again")
    resume.add_argument("ref", help="subscription id or profile URL")
    resume.set_defaults(func=cmd_follow_resume)

    feed = sub.add_parser("feed", help="JavDB actress releases")
    feed_sub = feed.add_subparsers(dest="feed_cmd", required=True)
    search = feed_sub.add_parser("search", help="resolve a name to JavDB actor cards")
    search.add_argument("name")
    search.set_defaults(func=cmd_feed_search)
    fadd = feed_sub.add_parser(
        "add",
        help="subscribe by search number, actor id, or actor URL",
    )
    fadd.add_argument("ref", nargs="?", help="search result number, actor id, or actor page URL")
    fadd.add_argument("--javdb-actor", help="JavDB actor id")
    fadd.add_argument("--name", help="display name")
    fadd.set_defaults(func=cmd_feed_add)
    feed_sub.add_parser("list", help="list actress subscriptions").set_defaults(func=cmd_feed_list)
    fremove = feed_sub.add_parser("remove", help="delete an actress subscription and its seen codes")
    fremove.add_argument("ref", help="subscription id, actor id, or actor page URL")
    fremove.set_defaults(func=cmd_feed_remove)
    fpause = feed_sub.add_parser("pause", help="skip an actress during check")
    fpause.add_argument("ref", help="subscription id, actor id, or actor page URL")
    fpause.set_defaults(func=cmd_feed_pause)
    fresume = feed_sub.add_parser("resume", help="include an actress during check again")
    fresume.add_argument("ref", help="subscription id, actor id, or actor page URL")
    fresume.set_defaults(func=cmd_feed_resume)

    check = sub.add_parser("check", help="poll subscriptions once (cron) or on a loop")
    mode = check.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="poll once and exit (default)")
    mode.add_argument(
        "--loop",
        type=float,
        metavar="MINUTES",
        help="repeat until interrupted; minimum 5 minutes",
    )
    check.add_argument("--dry-run", action="store_true", help="report without writing or notifying")
    check.add_argument(
        "--notify-existing",
        action="store_true",
        help="on the first look, notify for items already on the page",
    )
    check.add_argument("--quiet", action="store_true", help="print new items and the errors only")
    check.set_defaults(func=cmd_check)

    config = sub.add_parser("config", help="local configuration")
    config_sub = config.add_subparsers(dest="config_cmd", required=True)
    config_sub.add_parser("show", help="print settings with secrets redacted").set_defaults(
        func=cmd_config_show
    )
    config_sub.add_parser("path", help="print config and data paths").set_defaults(func=cmd_config_path)
    telegram = config_sub.add_parser("telegram", help="save a bot token and chat id")
    telegram.add_argument("--token")
    telegram.add_argument("--chat-id")
    telegram.set_defaults(func=cmd_config_telegram)
    set_cmd = config_sub.add_parser("set", help="set one config key")
    set_cmd.add_argument("key")
    set_cmd.add_argument("value")
    set_cmd.set_defaults(func=cmd_config_set)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if getattr(args, "data_dir", None):
        os.environ["HERALD_DATA_DIR"] = args.data_dir
    if getattr(args, "config", None):
        os.environ["HERALD_CONFIG"] = args.config
    try:
        return int(args.func(args))
    except HeraldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def cmd_follow_add(args: argparse.Namespace) -> int:
    parsed = parse_follow_url(args.url)
    title = _optional_title(args.title)
    with Database(data_dir() / "herald.db") as db:
        sub, created = db.add_subscription(
            kind="follow",
            provider=parsed.provider,
            target=parsed.target,
            url=parsed.url,
            title=title,
            now=utc_now(),
        )
    if created:
        print(f"added follow #{sub.id} {sub.provider} {sub.url}")
    else:
        print(f"already following #{sub.id} {sub.provider} {sub.url}")
    return 0


def cmd_follow_list(_args: argparse.Namespace) -> int:
    return _print_subs("follow")


def cmd_follow_remove(args: argparse.Namespace) -> int:
    return _remove("follow", args.ref)


def cmd_follow_pause(args: argparse.Namespace) -> int:
    return _set_enabled("follow", args.ref, False)


def cmd_follow_resume(args: argparse.Namespace) -> int:
    return _set_enabled("follow", args.ref, True)


def cmd_feed_search(args: argparse.Namespace) -> int:
    settings = load_settings()
    name = collapse(args.name)
    if not name or len(name) > 80:
        raise ConfigError("search name must be 1 to 80 characters")
    with make_client(settings) as client:
        hits = search_actors(client, settings, name)
    with Database(data_dir() / "herald.db") as db:
        db.replace_search(hits, utc_now())
    if not hits:
        print("no actor cards")
        return 0
    for index, hit in enumerate(hits, start=1):
        marker = MARKERS.get(hit.marker, hit.marker)
        tag = f" [{marker}]" if marker else ""
        print(f"#{index}  {hit.actor_id}  {hit.name}{tag}")
        if hit.aliases and hit.aliases != hit.name:
            print(f"    aliases: {hit.aliases}")
        print(f"    {hit.url}")
    names = [hit.name for hit in hits]
    if len(names) != len(set(names)):
        print(
            "Some names appear on more than one card "
            "(often a censored record and an uncensored one). "
            "Add the specific id; Herald does not choose."
        )
    print("Add a card with: herald feed add NUMBER")
    return 0


def cmd_feed_add(args: argparse.Namespace) -> int:
    if bool(args.ref) == bool(args.javdb_actor):
        raise ConfigError("pass a search number, an actor URL, an actor id, or --javdb-actor")
    settings = load_settings()
    title = _optional_title(args.name)
    with Database(data_dir() / "herald.db") as db:
        if args.javdb_actor:
            actor_id, url = parse_actor_ref(args.javdb_actor, settings.javdb_base)
        elif args.ref.isdigit():
            hit = db.get_search_hit(int(args.ref))
            if hit is None:
                raise HeraldError("no search result with that number; run `herald feed search` first")
            actor_id, url = parse_actor_ref(hit.actor_id, settings.javdb_base)
            if title is None:
                title = hit.name
        else:
            actor_id, url = parse_actor_ref(args.ref, settings.javdb_base)
        sub, created = db.add_subscription(
            kind="feed",
            provider="javdb",
            target=actor_id,
            url=url,
            title=title,
            now=utc_now(),
        )
    if created:
        print(f"added feed #{sub.id} javdb {sub.url}")
    else:
        print(f"already subscribed #{sub.id} javdb {sub.url}")
    return 0


def cmd_feed_list(_args: argparse.Namespace) -> int:
    return _print_subs("feed")


def cmd_feed_remove(args: argparse.Namespace) -> int:
    return _remove("feed", args.ref)


def cmd_feed_pause(args: argparse.Namespace) -> int:
    return _set_enabled("feed", args.ref, False)


def cmd_feed_resume(args: argparse.Namespace) -> int:
    return _set_enabled("feed", args.ref, True)


def cmd_check(args: argparse.Namespace) -> int:
    if args.loop is not None:
        if args.loop < 5:
            raise ConfigError("--loop interval must be at least 5 minutes")
        while True:
            run_once(args)
            if not args.quiet:
                print(f"sleeping {args.loop:g} minutes", file=sys.stderr)
            time.sleep(args.loop * 60)
    return run_once(args)


def cmd_config_show(_args: argparse.Namespace) -> int:
    file_cfg = load_file()
    settings = load_settings()
    warning = loose_permissions_warning(settings.config_path)
    if warning:
        print(warning, file=sys.stderr)
    print(f"config_file: {settings.config_path}")
    print(f"data_dir: {settings.data_dir}")
    print(f"telegram.token: {secret_source('TELEGRAM_BOT_TOKEN', file_cfg.telegram_token)}")
    chat = settings.telegram_chat_id or "(unset)"
    chat_from = "environment" if os.environ.get("TELEGRAM_CHAT_ID") else "config or unset"
    print(f"telegram.chat_id: {chat} ({chat_from})")
    print(f"http.min_delay_seconds: {settings.min_delay_seconds}")
    print(f"http.timeout_seconds: {settings.timeout_seconds}")
    print(f"http.javdb_base: {settings.javdb_base}")
    print(
        "credentials.simpcity_cookie: "
        f"{secret_source('SIMPCITY_COOKIE', file_cfg.simpcity_cookie)}"
    )
    user_id = settings.secrets.get("RULE34_USER_ID")
    print(f"credentials.rule34_user_id: {user_id or '(unset)'}")
    print(
        "credentials.rule34_api_key: "
        f"{secret_source('RULE34_API_KEY', file_cfg.rule34_api_key)}"
    )
    print(f"credentials.f95_cookie: {secret_source('F95_COOKIE', file_cfg.f95_cookie)}")
    print(f"credentials.javdb_cookie: {secret_source('JAVDB_COOKIE', file_cfg.javdb_cookie)}")
    print("JavDB requests wait at least 5 seconds between calls to that host.")
    return 0


def cmd_config_path(_args: argparse.Namespace) -> int:
    print(f"config: {config_path()}")
    print(f"data: {data_dir()}")
    print(f"database: {data_dir() / 'herald.db'}")
    return 0


def cmd_config_telegram(args: argparse.Namespace) -> int:
    updates: dict[str, str] = {}
    if args.token:
        updates["telegram.token"] = args.token
    if args.chat_id:
        updates["telegram.chat_id"] = args.chat_id
    if not updates:
        raise ConfigError("pass --token, --chat-id, or both")
    set_values(updates)
    print(f"saved Telegram settings in {config_path()}")
    return 0


def cmd_config_set(args: argparse.Namespace) -> int:
    if args.key not in SET_KEYS:
        known = ", ".join(sorted(key for key in SET_KEYS if "." in key))
        raise ConfigError(f"unknown setting {args.key!r}. Known keys: {known}")
    set_values({args.key: args.value})
    print(f"saved {args.key} in {config_path()}")
    return 0


def run_once(args: argparse.Namespace) -> int:
    settings = load_settings()
    if bool(settings.telegram_token) != bool(settings.telegram_chat_id):
        raise ConfigError("set both the Telegram token and the chat id, or leave both unset")
    warning = loose_permissions_warning(settings.config_path)
    if warning:
        print(warning, file=sys.stderr)
    try:
        with file_lock(data_dir() / "herald.lock"):
            return _run_locked(args, settings)
    except AlreadyRunning as exc:
        print(f"skip: {exc}", file=sys.stderr)
        return 0


def _run_locked(args: argparse.Namespace, settings) -> int:
    notifier = None
    with Database(data_dir() / "herald.db") as db, make_client(settings) as client:
        if settings.telegram_token and settings.telegram_chat_id:
            notifier = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id)
        try:

            def progress(sub) -> None:
                if not args.quiet:
                    print(f"checking #{sub.id} {sub.provider} {sub.label}", file=sys.stderr)

            report = run_check(
                settings,
                db,
                client,
                notifier=notifier,
                dry_run=args.dry_run,
                notify_existing=args.notify_existing,
                on_progress=progress,
            )
        finally:
            if notifier is not None:
                notifier.close()
    text = quiet_lines(report) if args.quiet else format_report(report)
    if text:
        print(text)
    for err in report.errors:
        print(f"error: {err}", file=sys.stderr)
    if report.telegram_missing:
        print(
            "error: new items are queued until a Telegram token and chat id are set",
            file=sys.stderr,
        )
    if report.telegram_error:
        print(f"error: {report.telegram_error}", file=sys.stderr)
    return report.exit_code


def _print_subs(kind: str) -> int:
    with Database(data_dir() / "herald.db") as db:
        rows = [sub for sub in db.list_subscriptions() if sub.kind == kind]
    if not rows:
        print(f"no {kind} subscriptions")
        return 0
    for sub in rows:
        if not sub.enabled:
            state = "paused"
        elif sub.baselined:
            state = "baseline"
        else:
            state = "new"
        print(f"#{sub.id}  {sub.provider}  {state}  {sub.title or '-'}")
        print(f"    {sub.url}")
        print(f"    checked={sub.last_checked_at or 'never'}  error={sub.last_error or '-'}")
    return 0


def _remove(kind: str, ref: str) -> int:
    settings = load_settings()
    with Database(data_dir() / "herald.db") as db:
        sub = _resolve(db, kind, ref, settings)
        db.delete(sub.id)
    print(f"removed {kind} #{sub.id} {sub.url}")
    return 0


def _set_enabled(kind: str, ref: str, enabled: bool) -> int:
    settings = load_settings()
    with Database(data_dir() / "herald.db") as db:
        sub = _resolve(db, kind, ref, settings)
        db.set_enabled(sub.id, enabled)
    print(f"{'resumed' if enabled else 'paused'} #{sub.id}")
    return 0


def _resolve(db: Database, kind: str, ref: str, settings):
    ref = ref.strip()
    if kind == "follow":
        if ref.isdigit():
            sub = db.get(int(ref))
            if sub is None:
                raise HeraldError(f"no subscription #{ref}")
        else:
            parsed = parse_follow_url(ref)
            sub = db.find("follow", parsed.provider, parsed.target)
            if sub is None:
                raise HeraldError("no matching follow subscription")
    elif ref.isdigit():
        sub = db.get(int(ref))
        if sub is None:
            sub = db.find("feed", "javdb", ref)
        if sub is None:
            raise HeraldError(f"no feed subscription matching {ref}")
    else:
        actor_id, _url = parse_actor_ref(ref, settings.javdb_base)
        sub = db.find("feed", "javdb", actor_id)
        if sub is None:
            raise HeraldError(f"no JavDB feed for actor {actor_id}")
    other = "feed" if kind == "follow" else "follow"
    if sub.kind != kind:
        raise HeraldError(f"#{sub.id} is a {sub.kind} subscription; use {other} remove")
    return sub


def _optional_title(value: str | None) -> str | None:
    if value is None:
        return None
    text = collapse(value)
    if not text:
        return None
    if len(text) > 120:
        raise ConfigError("name must be at most 120 characters")
    return text


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
