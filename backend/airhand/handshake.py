"""Handshake file publication.

The engine binds an ephemeral port, then publishes how to reach it. The write is atomic
(temp file + ``os.replace``) so a reader never observes a partially written file.

Browsers cannot read this file — in the packaged app Tauri's Rust layer reads it and passes the
values into the webview. See ``shared/protocol/README.md``.
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .jsonfile import atomic_write_json

APP_DIR_NAME = "AirHand"
HANDSHAKE_FILENAME = "runtime.json"


def app_data_dir() -> Path:
    """Per-user directory for everything the engine writes.

    Shared with the calibration profile, which lives beside the handshake but has a very different
    lifetime: the handshake is removed on clean shutdown, the profile must survive.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_RUNTIME_DIR") or (Path.home() / ".local" / "state")
    return Path(base) / APP_DIR_NAME


def default_handshake_path() -> Path:
    """Per-user runtime directory for the handshake file."""
    return app_data_dir() / HANDSHAKE_FILENAME


def generate_token() -> str:
    """A fresh token per launch.

    Loopback is not an authorization boundary on a multi-user machine: any local process can
    connect to the port, and this engine synthesizes real OS input.
    """
    return secrets.token_urlsafe(32)


def write_handshake(path: Path, *, port: int, token: str, protocol_version: str) -> None:
    payload: dict[str, Any] = {
        "pid": os.getpid(),
        "port": port,
        "protocolVersion": protocol_version,
        "token": token,
        "startedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    atomic_write_json(path, payload)


def remove_handshake(path: Path) -> None:
    """Remove the handshake file. Safe to call when it does not exist."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # A handshake we cannot delete is a stale-file problem for the next launch, not a reason
        # to fail shutdown. Readers validate liveness via `pid` precisely for this case.
        pass
