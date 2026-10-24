"""Exclusive lock so overlapping cron runs do not double-notify."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from herald.errors import AlreadyRunning
from herald.secure import harden_dir


@contextmanager
def file_lock(
    path: Path,
    *,
    message: str = "another herald check is already running",
) -> Iterator[None]:
    harden_dir(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(path, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise AlreadyRunning(message) from exc
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
