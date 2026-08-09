"""Recorded landmark traces — the format, the camera loop and the reader.

Shared by `bench_pointer.py` and `bench_pinch.py`. Both ask a different question of the same raw
material: a stream of **unfiltered** landmarks with timestamps, plus a label per frame saying what
the hand was asked to be doing at that moment.

Raw is the point. A trace of filtered landmarks could only ever be replayed through the filter that
produced it, which would make the tools useless for the thing they exist for — comparing settings
against identical input.

Not a package: `tools/` lands on `sys.path` when a script there is run, so a sibling import works.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

# Run from anywhere. Idempotent, so it does not matter that the calling script does this too.
_BACKEND = str(Path(__file__).resolve().parents[1])
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

TRACE_KIND = "airhand-trace"
TRACE_VERSION = 2

# Accepted for traces recorded before the format grew a second consumer. Same shape, older name.
LEGACY_KINDS = ("airhand-pointer-trace",)


@dataclass(frozen=True)
class Trace:
    header: dict[str, Any]
    frames: list[dict[str, Any]]

    @property
    def frame_aspect(self) -> float:
        return self.header["frameWidth"] / self.header["frameHeight"]

    @property
    def fps(self) -> float:
        span = self.frames[-1]["t"] - self.frames[0]["t"] if len(self.frames) > 1 else 0.0
        return (len(self.frames) - 1) / span if span > 0 else 0.0

    def segment(self, name: str) -> list[dict[str, Any]]:
        return [frame for frame in self.frames if frame.get("segment") == name]


def load(path: Path) -> Trace:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SystemExit(f"{path} is empty")

    header = json.loads(lines[0])
    if header.get("kind") not in (TRACE_KIND, *LEGACY_KINDS):
        raise SystemExit(f"{path} is not an AirHand trace")

    return Trace(header, [json.loads(line) for line in lines[1:] if line])


def record(
    path: Path,
    *,
    duration: float,
    label: Callable[[float], dict[str, Any]],
    instructions: Iterator[str] | list[str] = (),
    width: int = 640,
    height: int = 480,
    camera_index: int = 0,
) -> int:
    """Capture raw landmarks for `duration` seconds, labelling each frame via `label(elapsed)`.

    `label` returns whatever the caller wants stored alongside the frame — a segment name, an
    attempt number — and may print its own prompts as the script advances. Keeping the schedule in
    the caller is what lets one recorder serve a two-part sweep and a metronome of pinches without
    either knowing about the other.

    Returns a process exit code, so a missing camera is reported rather than raised.
    """
    from airhand.camera import CameraService, CameraUnavailable
    from airhand.tracking import TrackingEngine

    import cv2

    camera = CameraService(index=camera_index, width=width, height=height)
    engine = TrackingEngine()

    try:
        info = camera.open()
    except CameraUnavailable as exc:
        print(f"Camera unavailable: {exc}")
        print("Is the engine still running? OpenCV holds the webcam exclusively on Windows.")
        return 1

    engine.open()
    path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Recording from {info.name} at {info.width}x{info.height}.")
    print()
    for line in instructions:
        print(f"  {line}")
    print()
    for count in (3, 2, 1):
        print(f"  starting in {count}...")
        time.sleep(1.0)

    frames = 0
    detected = 0

    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "kind": TRACE_KIND,
                    "version": TRACE_VERSION,
                    "frameWidth": info.width,
                    "frameHeight": info.height,
                }
            )
            + "\n"
        )

        started = time.monotonic()
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= duration:
                break

            annotation = label(elapsed)

            frame = camera.read()
            if frame is None:
                break
            now = time.monotonic()
            result = engine.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), int(elapsed * 1000))
            frames += 1
            if result.landmarks:
                detected += 1

            handle.write(
                json.dumps({"t": now - started, **annotation, "landmarks": result.landmarks}) + "\n"
            )

    engine.close()
    camera.close()

    print()
    print(f"Wrote {frames} frames to {path} ({detected} with a hand, {frames - detected} without).")
    if frames and detected / frames < 0.8:
        print("Detection was patchy — check the lighting before trusting these numbers.")
    return 0
