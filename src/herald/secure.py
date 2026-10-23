"""Private files for config, the database, and the process lock."""

from __future__ import annotations

import os
from pathlib import Path


def harden_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def harden_file(path: Path) -> None:
    if path.exists():
        os.chmod(path, 0o600)


def write_private(path: Path, text: str) -> None:
    """Write text to ``path`` as mode 0600, replacing it atomically."""
    harden_dir(path.parent)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def mode_bits(path: Path) -> int:
    return path.stat().st_mode & 0o777
