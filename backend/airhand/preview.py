"""Video preview encoder — the camera image, cheap enough to be free.

The desktop UI renders these frames blurred and full-bleed behind the interface, which is the
whole reason this can be aggressive: resolution and quality above what a blur can show are pure
cost. Three rules follow from that, and they are the class's entire job:

- **Opt-in.** Nobody watching costs nothing. `enabled` starts False and is driven by whether a
  client actually asked.
- **Downscale before encoding.** Encode time and bytes both scale with pixel count.
- **Throttled below the tracking rate.** The pipeline thread that calls this is the same one that
  drives the cursor, so the preview gets a slice of the budget, not a share of every frame.

Kept out of `live.py` deliberately: it needs neither a camera nor a cursor, so it can be tested
without touching either.
"""

from __future__ import annotations

import threading
from typing import Any

import cv2

from .protocol import preview_fps, preview_max_width

# JPEG quality. The client blurs these into a background, so quality above this buys nothing
# visible and costs bytes on every frame.
JPEG_QUALITY = 55


class PreviewEncoder:
    """Holds the most recently encoded preview frame, or nothing at all."""

    def __init__(
        self, *, max_width: int | None = None, fps: float | None = None, quality: int = JPEG_QUALITY
    ) -> None:
        self._max_width = max_width if max_width is not None else preview_max_width()
        self._interval = 1.0 / (fps if fps is not None else preview_fps())
        self._quality = quality

        self._lock = threading.Lock()
        self._enabled = False
        self._frame: bytes | None = None
        self._index = 0
        self._last_encoded_at = 0.0

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def max_width(self) -> int:
        return self._max_width

    @property
    def interval(self) -> float:
        return self._interval

    def set_enabled(self, enabled: bool) -> bool:
        """Turn encoding on or off. Returns True if this call changed anything."""
        with self._lock:
            if self._enabled == enabled:
                return False
            self._enabled = enabled
            if not enabled:
                # Drop the frame rather than let it be served after the viewer left — a preview
                # that outlives the stream shows a frozen image that looks live.
                self._frame = None
        return True

    def clear(self) -> None:
        with self._lock:
            self._frame = None

    def latest(self) -> tuple[int, bytes] | None:
        """Newest frame as ``(index, jpeg_bytes)``.

        The index is monotonic, exactly like ``Sample.frame_index``: it is how a consumer tells a
        fresh frame from a repeated read of the same one.
        """
        with self._lock:
            if self._frame is None:
                return None
            return self._index, self._frame

    def should_encode(self, now: float) -> bool:
        """Whether this frame is due. Cheap enough to call on every pipeline iteration."""
        with self._lock:
            return self._enabled and (now - self._last_encoded_at) >= self._interval

    def encode(self, frame: Any, now: float) -> bool:
        """Downscale and JPEG-encode one BGR frame. Returns False if OpenCV refused it."""
        height, width = frame.shape[:2]
        if width > self._max_width:
            scale = self._max_width / width
            # INTER_AREA is the correct filter for shrinking: it averages rather than samples, so
            # the result does not alias into the blur as shimmering edges.
            frame = cv2.resize(
                frame,
                (self._max_width, max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )

        ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._quality])
        if not ok:
            return False

        payload = buffer.tobytes()
        with self._lock:
            self._last_encoded_at = now
            self._index += 1
            self._frame = payload
        return True
