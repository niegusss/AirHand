"""Protocol constants and message builders.

The protocol version is read from ``shared/protocol/protocol.json`` — the same file the React
client reads — so the two sides cannot drift apart. Never hardcode the version here.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

ENGINE_VERSION = "0.2.1"

CameraState = Literal["off", "starting", "on", "error"]
TrackingState = Literal["idle", "running", "paused"]
Gesture = Literal["none", "move", "left_click", "right_click", "drag", "scroll"]
ErrorCode = Literal[
    "unauthorized",
    "protocol_mismatch",
    "camera_unavailable",
    "internal",
    # Added in 1.6.0. A settings patch the engine will not accept — the previous values stand.
    "invalid_settings",
]


def _protocol_file() -> Path:
    """Locate protocol.json in both development and PyInstaller-frozen layouts."""
    if getattr(sys, "frozen", False):
        # PyInstaller unpacks bundled data files into _MEIPASS. The spec file must add
        # shared/protocol/protocol.json as a data file, or this raises at startup — which is
        # the correct failure: a frozen engine with no protocol definition is not runnable.
        return Path(getattr(sys, "_MEIPASS")) / "protocol.json"
    return Path(__file__).resolve().parents[2] / "shared" / "protocol" / "protocol.json"


@lru_cache(maxsize=1)
def protocol_spec() -> dict[str, Any]:
    path = _protocol_file()
    if not path.is_file():
        raise FileNotFoundError(
            f"Protocol definition not found at {path}. "
            "In a frozen build, bundle shared/protocol/protocol.json as a data file."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def protocol_version() -> str:
    return str(protocol_spec()["version"])


def landmark_count() -> int:
    return int(protocol_spec()["landmarkCount"])


def preview_max_width() -> int:
    """Longest edge of a preview frame, in pixels.

    The client renders preview frames blurred and full-bleed, so resolution above this is thrown
    away by the blur while still costing encode time and bandwidth linearly.
    """
    return int(protocol_spec()["previewMaxWidth"])


def preview_fps() -> float:
    """Preview frame rate. Deliberately below the tracking rate — see ``LiveSource``."""
    return float(protocol_spec()["previewFps"])


def hello() -> dict[str, Any]:
    return {
        "type": "hello",
        "protocolVersion": protocol_version(),
        "engineVersion": ENGINE_VERSION,
        # "preview" is advertised by the engine, not by the source: whether a *particular* source
        # can produce frames is a separate question, answered by frames simply not arriving.
        "capabilities": ["telemetry", "preview", "settings", "calibration", "cameras"],
    }


def cameras(
    *,
    devices: list[dict[str, Any]],
    selected: int | None,
    scanning: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """The `cameras` server→client message. Added in 1.10.0.

    Broadcast rather than answered to whoever asked, for the same reason as `settings` and
    `calibration`: a scan restarts the pipeline, so every connected window has to learn about it.

    `selected` is what the engine will open *next*, which is not always what it has open now — a
    device chosen while the pipeline is stopped takes effect on the next start. `status.cameraIndex`
    answers the other question.
    """
    return {
        "type": "cameras",
        # True only while a probe is in flight. It exists so the UI can disable the control rather
        # than let a second scan stack up behind the first.
        "scanning": scanning,
        "devices": devices,
        "selected": selected,
        # Why a scan or a selection could not be honoured, meant to be shown. None on success.
        "reason": reason,
    }


def status(
    *,
    camera: CameraState,
    tracking: TrackingState,
    camera_name: str | None = None,
    camera_index: int | None = None,
    cpu_percent: float = 0.0,
    message: str | None = None,
    frame_width: int | None = None,
    frame_height: int | None = None,
    cursor_available: bool = False,
    cursor_enabled: bool = False,
    cursor_reason: str | None = None,
    cursor_dry_run: bool = False,
    killswitch_hotkey: str | None = None,
) -> dict[str, Any]:
    return {
        "type": "status",
        "camera": camera,
        "tracking": tracking,
        "cameraName": camera_name,
        # Added in 1.10.0. `cameraName` embeds the index in a display string, and matching a device
        # against the list by parsing that string would be exactly the kind of second copy this
        # protocol keeps removing. Null until a source declares one.
        "cameraIndex": camera_index,
        "cpuPercent": round(cpu_percent, 1),
        "message": message,
        # Added in 1.5.0. Landmarks are normalized against these, so a client that draws them
        # into a container of a different shape stretches the hand. Null until the camera opens.
        "frameWidth": frame_width,
        "frameHeight": frame_height,
        # Added in protocol 1.3.0. `cursorEnabled` is authoritative: the engine can disable
        # actuation on its own (kill-switch, client disconnect, pause), so the UI must render
        # this rather than remember what it last asked for.
        "cursorAvailable": cursor_available,
        "cursorEnabled": cursor_enabled,
        "cursorReason": cursor_reason,
        "cursorDryRun": cursor_dry_run,
        "killswitchHotkey": killswitch_hotkey,
    }


def error(code: ErrorCode, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}
