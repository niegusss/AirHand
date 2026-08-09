"""Atomic JSON file I/O.

Two files are written to the user's machine — the runtime handshake and the calibration profile —
and both have the same requirement: a reader must never observe a half-written file. The handshake
is read by a separate process (Tauri) that may be polling for it; the profile is read at startup
after a shutdown that may have been a hard kill.

So the write is temp-file-plus-rename. `os.replace` is atomic on Windows and POSIX alike, which is
what makes "either the old file or the new one, never a mixture" true rather than likely.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write `payload` to `path` atomically, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # The temp file must live in the same directory as the target: os.replace is only atomic
    # within a filesystem, and a temp directory can easily be on another volume.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave a stray temp file behind on failure — a directory slowly filling with
        # `.profile-xxxx.tmp` is a confusing way to discover that writes have been failing.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON object, or None if it is missing, unreadable or not an object.

    Tolerant on purpose. These files are user-visible on disk and can be edited, truncated by a
    crash, or left behind by an older version. None means "no usable content" and every caller
    treats that the same way as "no file" — which is the only behaviour that keeps a corrupt
    profile from bricking startup.
    """
    try:
        # `utf-8-sig`, not `utf-8`: it reads plain UTF-8 unchanged and additionally strips a byte
        # order mark. These files are user-visible and documented as inspectable, and every common
        # Windows editor — Notepad, PowerShell's `Out-File -Encoding utf8` — adds one. Plain
        # `utf-8` turns that into "the file is not JSON", which a user would experience as their
        # calibration silently resetting because they opened it.
        parsed = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None
