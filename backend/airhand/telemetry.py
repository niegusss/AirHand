"""Synthetic telemetry source.

Kept after the real pipeline landed, for two reasons: UI work does not need a webcam attached, and
it isolates frontend bugs from camera problems. Select it with `--source synthetic`.

It **poses a hand and classifies it with the real Gesture Engine** rather than asserting a label.
An earlier version fabricated the gesture string directly, which meant its landmarks and its label
could disagree — the overlay showed an open hand while the readout claimed a pinch. Driving the
real engine makes the demo self-consistent by construction and exercises the classification path
that the camera source uses.

Touches no camera, no MediaPipe and no OS cursor.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from .calibration import CalibrationResult, CalibrationRunner, Observation
from .cursor.engine import CursorState
from .gestures import GestureDebug, GestureEngine, palm_center
from .handmodel import FIST, POINTING, SCROLL_POSE, HandPose, make_hand
from .pipeline import Sample, SourceStatus
from .settings import DEFAULTS, EngineSettings

# The synthetic frame is square, so no aspect correction is needed.
SYNTHETIC_ASPECT = 1.0

# Reported to clients so they can shape the landmark overlay. Square, matching SYNTHETIC_ASPECT —
# every source declares the geometry its landmarks are normalized against, this one included.
SYNTHETIC_FRAME = 480

_OPEN = 0.9
_CLOSED = 0.15


@dataclass(frozen=True)
class _Step:
    """One scripted pose and how long to hold it."""

    duration: float
    pose: HandPose = POINTING
    pinch_index: float | None = _OPEN
    pinch_middle: float | None = None


# A tour of every gesture the engine can produce. Durations straddle the engine's own thresholds:
# the short pinches resolve as clicks, the long one crosses into a drag.
_SCRIPT: tuple[_Step, ...] = (
    _Step(1.2, pose=FIST),                                   # none
    _Step(2.5),                                              # move
    _Step(0.2, pinch_index=_CLOSED),                         # pinch, undecided
    _Step(1.5),                                              # release -> left click, then move
    _Step(0.2, pinch_index=None, pinch_middle=_CLOSED),      # pinch middle, undecided
    _Step(1.5),                                              # release -> right click, then move
    _Step(1.6, pinch_index=_CLOSED),                         # held past threshold -> drag
    _Step(1.2),                                              # release, no click after a drag
    _Step(2.2, pose=SCROLL_POSE),                            # scroll
)


class SyntheticSource:
    """Generates a believable telemetry stream from elapsed time alone."""

    def __init__(
        self,
        *,
        target_fps: float = 60.0,
        seed: int | None = None,
        settings: EngineSettings | None = None,
    ) -> None:
        self._target_fps = target_fps
        self._rng = random.Random(seed)
        self._cycle_length = sum(step.duration for step in _SCRIPT)
        self._settings = settings or DEFAULTS
        self._engine = GestureEngine(config=self._settings.gesture)
        self._calibration = CalibrationRunner()
        self._started_at: float | None = None
        self._frame_index = 0

    def apply_settings(self, settings: EngineSettings) -> None:
        """Gesture thresholds genuinely take effect here — this source runs the real classifier.

        Pointer and cursor values are stored but do nothing: there is no cursor behind this source,
        which it already reports honestly via `cursor_state()`. Storing them keeps the Calibration
        screen fully exercisable without a webcam, which is the reason this source exists.
        """
        self._settings = settings
        self._engine.config = settings.gesture

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._frame_index = 0
        self._engine.reset()

    def stop(self) -> None:
        self._started_at = None
        self._engine.reset()
        # No more frames are coming, so a session would sit at "sampling" forever.
        self._calibration.cancel()

    def cursor_state(self) -> CursorState:
        """Synthetic telemetry must never drive the real cursor.

        Its hand follows a scripted path, so actuating it would move the pointer on its own with
        no user input at all. Reporting unavailable is the honest answer, not a limitation.
        """
        return CursorState(
            available=False,
            enabled=False,
            reason="The synthetic source never actuates the cursor — its hand is scripted.",
        )

    def set_cursor_enabled(self, enabled: bool) -> CursorState:
        return self.cursor_state()

    def set_preview_enabled(self, enabled: bool) -> None:
        """Accepted and ignored — there is no camera behind this source to preview."""

    def latest_preview(self) -> tuple[int, bytes] | None:
        """Always None.

        Rendering the scripted hand into a fake camera image would be a convincing lie: the UI
        would show a live-looking feed for a source that never opened a camera. The client's
        fallback background is the honest answer.
        """
        return None

    def status(self) -> SourceStatus:
        if self._started_at is None:
            return SourceStatus(camera="off", message="Synthetic source stopped")
        return SourceStatus(
            camera="on",
            camera_name="Synthetic source",
            message="Synthetic telemetry — no camera or MediaPipe in this mode",
            frame_width=SYNTHETIC_FRAME,
            frame_height=SYNTHETIC_FRAME,
        )

    def latest(self) -> Sample | None:
        if self._started_at is None:
            return None
        self._frame_index += 1
        return self._sample(time.monotonic() - self._started_at)

    def _step_at(self, elapsed: float) -> _Step:
        position = elapsed % self._cycle_length
        for step in _SCRIPT:
            if position < step.duration:
                return step
            position -= step.duration
        return _SCRIPT[-1]

    def _wrist_center(self, elapsed: float) -> tuple[float, float]:
        # Lissajous path keeps the hand moving without ever repeating exactly, which makes
        # smoothing and jitter behaviour in the UI easier to eyeball.
        return (
            0.5 + 0.16 * math.sin(elapsed * 0.7),
            0.62 + 0.10 * math.sin(elapsed * 1.1 + 0.5),
        )

    def start_calibration(self, step: str) -> CalibrationResult:
        return self._calibration.start(step, settings=self._settings, now=time.monotonic())

    def cancel_calibration(self) -> None:
        self._calibration.cancel()

    def calibration(self) -> CalibrationResult | None:
        return self._calibration.result()

    def _observe_calibration(
        self, *, anchor: tuple[float, float] | None, debug: GestureDebug | None, detected: bool
    ) -> None:
        """Feed the scripted hand into a measurement, exactly as the live pipeline does.

        Worth supporting rather than stubbing out: this source exists so the Calibration screen can
        be exercised without a webcam, and a wizard whose measurement step is a dead end in that
        mode would only be half-testable. The script pinches, so the pinch step reaches a verdict.
        """
        self._calibration.observe(
            Observation(
                anchor=anchor,
                pinch_index=debug.pinch_index if debug else None,
                detected=detected,
            ),
            now=time.monotonic(),
        )

    def _sample(self, elapsed: float) -> Sample:
        step = self._step_at(elapsed)

        # Frame time wanders around the target with occasional small hitches, so the UI has
        # something realistic to smooth and chart.
        jitter = self._rng.uniform(-2.5, 2.5)
        hitch = 6.0 if self._rng.random() < 0.02 else 0.0
        fps = max(12.0, self._target_fps + jitter - hitch)
        inference_ms = self._rng.uniform(6.0, 12.0)
        latency_ms = inference_ms + self._rng.uniform(1.0, 3.0)
        # Whatever is left of the frame budget is modelled as time waiting on the camera.
        capture_ms = max(0.0, (1000.0 / fps) - latency_ms)

        landmarks = make_hand(
            step.pose,
            scale=0.22,
            center=self._wrist_center(elapsed),
            aspect=SYNTHETIC_ASPECT,
            pinch_index=step.pinch_index,
            pinch_middle=step.pinch_middle,
        )

        update = self._engine.update(landmarks, aspect=SYNTHETIC_ASPECT, now=elapsed)
        gesture, debug = update.gesture, update.debug

        hand_detected = step.pose is not FIST
        if not hand_detected:
            # A closed fist is the script's stand-in for "nothing to track".
            self._observe_calibration(anchor=None, debug=debug, detected=False)
            return Sample(
                fps=fps,
                latency_ms=latency_ms,
                capture_ms=capture_ms,
                inference_ms=inference_ms,
                hand_detected=False,
                handedness=None,
                gesture=gesture,
                landmarks=None,
                cursor=None,
                frame_index=self._frame_index,
                gesture_debug=debug.to_message() if debug else None,
            )

        # The palm centroid, matching the real pipeline's cursor anchor (see `pointer.py`). It was
        # the index fingertip until 2026-08-08, which drifted from the engine the day the anchor
        # moved — a demo that disagrees with the thing it demonstrates is worse than no demo.
        anchor = palm_center(landmarks)
        self._observe_calibration(anchor=anchor, debug=debug, detected=True)
        return Sample(
            fps=fps,
            latency_ms=latency_ms,
            capture_ms=capture_ms,
            inference_ms=inference_ms,
            hand_detected=True,
            handedness="right",
            gesture=gesture,
            landmarks=landmarks,
            cursor={"x": round(anchor[0], 4), "y": round(anchor[1], 4)},
            frame_index=self._frame_index,
            gesture_debug=debug.to_message() if debug else None,
        )
