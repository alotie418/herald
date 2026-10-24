"""Telegram long-poll that subscribes JavDB actress feeds.

`herald bot` only accepts messages from the configured chat. It searches and
stores a subscription. It does not poll actor pages or send release notices;
`herald check` still does that on its own schedule.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable

from herald.config import Settings
from herald.db import Database
from herald.errors import HeraldError
from herald.feeds import add_javdb_feed
from herald.models import ActorHit
from herald.notify import TelegramNotifier
from herald.util import clip, collapse, utc_now

Search = Callable[[str], list[ActorHit]]
MARKERS = {"censored": "有码", "uncensored": "无码"}
HELP = """\
向这个对话发送 JavDB 演员名即可订阅新作。

只处理配置里的那一个 chat id。只找到一张卡时直接订阅；找到多张时回复序号，Herald 不会自动选择有码或无码。

/start  /help  查看说明
/list  列出已订阅的 JavDB 演员
/cancel  放弃当前序号

订阅不会去拉作品页。新番号仍由 herald check 检查并通知。\
"""


def run_bot(
    settings: Settings,
    db: Database,
    notifier: TelegramNotifier,
    search: Search,
    *,
    poll_timeout: int = 25,
    max_polls: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    offset: int | None = None
    polls = 0
    while max_polls is None or polls < max_polls:
        polls += 1
        try:
            updates = notifier.get_updates(offset=offset, timeout=poll_timeout)
        except HeraldError as exc:
            if max_polls is not None:
                raise
            print(f"error: {exc}", file=sys.stderr)
            sleep_fn(5)
            continue
        if not updates:
            continue
        for update in updates:
            if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                continue
            update_id = int(update["update_id"])
            try:
                reply = handle_update(update, settings=settings, db=db, search=search)
                if reply:
                    notifier.send(reply)
            except HeraldError as exc:
                print(f"error: {exc}", file=sys.stderr)
                if max_polls is not None:
                    raise
                sleep_fn(5)
                return
            offset = update_id + 1


def handle_update(
    update: dict,
    *,
    settings: Settings,
    db: Database,
    search: Search,
) -> str | None:
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if str(chat_id) != settings.telegram_chat_id:
        return None
    text = message.get("text")
    if not isinstance(text, str) or not text.strip():
        return "请发送演员名，或用 /help 查看命令。"
    return handle_text(text, settings=settings, db=db, search=search)


def handle_text(
    text: str,
    *,
    settings: Settings,
    db: Database,
    search: Search,
) -> str:
    command = _command(text)
    if command is not None:
        if command in {"start", "help"}:
            return HELP
        if command == "list":
            return _list_feeds(db)
        if command == "cancel":
            return _cancel(db)
        return HELP
    choice = re.fullmatch(r"#?(\d+)", text.strip())
    if choice:
        return _pick(int(choice.group(1)), settings, db)
    return _search_name(text, settings, db, search)


def _command(text: str) -> str | None:
    token = text.strip().split(maxsplit=1)[0]
    if not token.startswith("/"):
        return None
    return token[1:].split("@", 1)[0].lower()


def _list_feeds(db: Database) -> str:
    rows = [sub for sub in db.list_subscriptions() if sub.kind == "feed" and sub.provider == "javdb"]
    if not rows:
        return "还没有 JavDB 订阅。"
    lines = ["JavDB 订阅："]
    for sub in rows:
        paused = "（已暂停）" if not sub.enabled else ""
        lines.append(f"#{sub.id} {sub.label}{paused}")
        lines.append(sub.url)
    return "\n".join(lines)


def _cancel(db: Database) -> str:
    if not db.list_search():
        return "没有待选结果。"
    db.replace_search([], utc_now())
    return "已取消选择。"


def _pick(index: int, settings: Settings, db: Database) -> str:
    if not db.list_search():
        return "没有待选结果。直接发送演员名即可搜索。"
    hit = db.get_search_hit(index)
    if hit is None:
        return "没有这个序号。回复列表里的数字，或发送 /cancel。"
    return _subscribe(hit, settings, db)


def _search_name(text: str, settings: Settings, db: Database, search: Search) -> str:
    name = collapse(text)
    if not name or len(name) > 80:
        return "演员名需要 1 到 80 个字。"
    try:
        hits = search(name)
    except HeraldError as exc:
        return f"搜索失败：{clip(str(exc), 350)}"
    if not hits:
        db.replace_search([], utc_now())
        return f"没有找到「{name}」。"
    if len(hits) == 1:
        db.replace_search([], utc_now())
        return _subscribe(hits[0], settings, db)
    db.replace_search(hits, utc_now())
    lines = [f"找到 {len(hits)} 个结果。回复序号订阅，Herald 不会自动选择。"]
    lines.extend(_format_hit(index, hit) for index, hit in enumerate(hits, start=1))
    lines.append("发送 /cancel 取消。")
    return "\n".join(lines)


def _subscribe(hit: ActorHit, settings: Settings, db: Database) -> str:
    sub, created = add_javdb_feed(db, settings, hit.actor_id, hit.name)
    verb = "已订阅" if created else "已经订阅"
    return f"{verb} #{sub.id} {hit.name}\n{sub.url}"


def _format_hit(index: int, hit: ActorHit) -> str:
    marker = MARKERS.get(hit.marker, "")
    tag = f" [{marker}]" if marker else ""
    return f"{index}. {hit.name}{tag} {hit.actor_id}\n{hit.url}"
