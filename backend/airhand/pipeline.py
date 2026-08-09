"""Shared pipeline types.

Defines the boundary the WebSocket server sees: it knows how to ask a source for the latest
sample and its status, and nothing else. Swapping the synthetic source for the real camera +
MediaPipe pipeline must not require any change to the server or the wire format — that is the
engine/UI separation from systemPatterns.md applied one level down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .cursor.engine import CursorState
from .protocol import CameraState, Gesture

if TYPE_CHECKING:
    # Only for the annotations: `settings` imports `pointer`, which imports `gestures`, which
    # imports `protocol` — importing it for real here would close the loop. `calibration` sits on
    # top of `settings`, so it inherits the same restriction.
    from .calibration import CalibrationResult
    from .settings import EngineSettings


@dataclass(frozen=True)
class SourceStatus:
    camera: CameraState
    camera_name: str | None = None
    message: str | None = None
    """Geometry the source's landmarks are normalized against.

    MediaPipe normalizes `x` by frame width and `y` by height, so a client cannot draw a landmark
    without distortion unless it knows the shape of the frame they came from. There is no
    reference webcam (`PM-QUESTIONS.md` #6), so this cannot be assumed — it has to be reported.

    None until the camera is open.
    """
    frame_width: int | None = None
    frame_height: int | None = None


@dataclass
class Sample:
    """One processed frame's worth of telemetry."""

    fps: float
    """In-process time from a frame arriving to landmarks being ready. Excludes sensor exposure,
    driver buffering, transport and render — so it is a floor for end-to-end latency."""
    latency_ms: float
    hand_detected: bool
    handedness: str | None
    gesture: Gesture
    landmarks: list[list[float]] | None
    cursor: dict[str, float] | None
    """Monotonic sequence number — lets consumers tell a fresh frame from a repeated read."""
    frame_index: int = 0
    """Time blocked waiting for the camera to deliver a frame.

    This explains THROUGHPUT, not latency: the frame is fresh when it arrives, so the wait is
    idle time, not staleness. If FPS is low while `latency_ms` is small, this is where it went.
    """
    capture_ms: float = 0.0
    """The model's share of `latency_ms` — the rest is colour conversion."""
    inference_ms: float = 0.0
    """Raw feature values behind the classification.

    Present so gesture thresholds can be tuned by looking at the numbers rather than guessing.
    None when no hand is present or the engine produced no reading.
    """
    gesture_debug: dict[str, Any] | None = None

    def to_message(self, ts: float) -> dict[str, Any]:
        message: dict[str, Any] = {
            "type": "telemetry",
            "ts": ts,
            "fps": round(self.fps, 1),
            "latencyMs": round(self.latency_ms, 1),
            "captureMs": round(self.capture_ms, 1),
            "inferenceMs": round(self.inference_ms, 1),
            "handDetected": self.hand_detected,
            "handedness": self.handedness,
            "gesture": self.gesture,
            "landmarks": self.landmarks,
            "cursor": self.cursor,
        }
        # Omitted rather than sent as null when absent — it rides on every frame at 30 Hz, and
        # there is no information in a null.
        if self.gesture_debug is not None:
            message["gestureDebug"] = self.gesture_debug
        return message


@runtime_checkable
class TelemetrySource(Protocol):
    """Anything that can produce telemetry: synthetic generator or the real CV pipeline.

    Cursor actuation belongs here rather than on the server because the source owns the gesture
    events that drive it. A source that cannot actuate — the synthetic one — reports
    `available=False` rather than silently accepting an enable it will not honour.
    """

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def status(self) -> SourceStatus: ...

    def latest(self) -> Sample | None: ...

    def cursor_state(self) -> CursorState: ...

    def set_cursor_enabled(self, enabled: bool) -> CursorState: ...

    def set_preview_enabled(self, enabled: bool) -> None:
        """Opt in to producing preview frames.

        Off by default and gated on a client actually asking, because encoding costs frame budget
        in the same thread that drives the cursor. Nobody watching must cost nothing.
        """

    def latest_preview(self) -> tuple[int, bytes] | None:
        """Newest preview frame as ``(index, jpeg_bytes)``, or None if there is none.

        The index is a monotonic counter, exactly like :attr:`Sample.frame_index`: it is how a
        consumer tells a fresh frame from a repeated read of the same one.
        """
        return None

    def apply_settings(self, settings: "EngineSettings") -> None:
        """Adopt already-validated settings.

        Validation, persistence and the decision to accept happen above this line — a source is
        told what the settings now are, it does not get a vote. Called from the server thread while
        the pipeline runs, so an implementation must not block on the pipeline.
        """

    def start_calibration(self, step: str) -> "CalibrationResult":
        """Begin a measurement over the frames this source is about to produce.

        Lives on the source rather than the server because only the source sees per-frame anchors
        and pinch distances — the server samples telemetry at its own rate and would measure the
        gaps between its polls as much as the hand.
        """
        raise NotImplementedError

    def cancel_calibration(self) -> None:
        """Abandon any measurement in progress. Safe to call when there is none."""

    def calibration(self) -> "CalibrationResult | None":
        """Latest measurement state, or None if none has been taken since the source started."""
        return None
