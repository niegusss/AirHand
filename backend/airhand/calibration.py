"""Calibration measurement — turning a few seconds of a real hand into settings.

Why this lives in the engine at all: thresholds here are expressed in multiples of hand scale
against MediaPipe's landmark placement, so deriving one is computer-vision work and belongs on
this side of the wire. The Calibration screen asks for a measurement, shows what came back, and
sends an ordinary `set_settings` if the user accepts it.

**A suggestion is shaped as a settings patch.** That is the load-bearing decision: accepting a
measurement is the same code path as dragging a slider, with the same validation and the same
persistence. There is no second way to write a setting, so there is no second way for one to be
wrong.

**Nothing here is min/max and nothing is a mean.** Detection drops frames, an occluded thumb
throws a single wild reading, and a hand told to hold still drifts. Every derivation below uses a
median or a percentile so that no single frame can define the result — a lesson this project has
now paid for twice, in the rest-jitter metric and in the lost clicks.

Pure: no I/O, no OpenCV, no threads. It is fed one observation per frame and asked for a result.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Literal, Sequence, cast

from .settings import EngineSettings, bounds_for

Step = Literal["neutral", "reach", "pinch"]
State = Literal["sampling", "done", "failed"]

# How long each measurement runs. The pinch step gets the most because the user has to perform
# three separate deliberate actions inside it, at their own pace.
STEP_SECONDS: dict[Step, float] = {"neutral": 3.0, "reach": 6.0, "pinch": 8.0}

# Continuous absence that ends a session early. This is meant to catch "the user walked away", not
# "detection hiccupped" — dropouts are routine, and a pinch is the moment MediaPipe is *most* likely
# to lose the hand.
#
# Two seconds, not one. The `reach` step asks the user to go as far as is comfortable, which
# regularly takes the hand to the edge of the frame and briefly out of it; a one-second rule failed
# the very measurement it was meant to protect. Sessions that lose the hand but keep going are
# still caught at the end by MIN_SAMPLES, which is the check that actually knows whether enough
# was seen.
LOST_TOLERANCE_SECONDS = 2.0

# Below this many frames with a hand in them there is nothing worth concluding.
MIN_SAMPLES = 15

MIN_PINCH_ATTEMPTS = 2

# How far below the resting level a reading has to fall to count as a deliberate pinch rather than
# noise, and how many consecutive frames it has to stay there.
ATTEMPT_DEPTH = 0.15
MIN_ATTEMPT_SAMPLES = 2

# Where the threshold sits in the measured gap between a firm pinch and the resting hand.
#
# **Proportional, not a fixed offset.** A first version added a flat 0.08 above the worst attempt,
# and a real measurement (2026-08-08: worst 0.18, resting 1.16) exposed it: in a gap 0.98 wide it
# produced 0.26, hugging the pinch side and leaving nearly the whole range unused. That is the
# pass-10 bug reproduced — eight seconds of deliberate pinches only ever contains *clean* ones, and
# the clicks that get lost are the ones where an occluded thumb pushes the estimate up. The width
# of the gap is the information the measurement bought; the margin has to spend it.
#
# 0.35 rather than a half: the resting level is a p90, so there is more room above the threshold
# than below it, and the pinch side is where a missed click costs the user something.
PINCH_GAP_FRACTION = 0.35

# Floor for a genuinely narrow gap, where a fraction would leave no headroom at all.
MIN_PINCH_MARGIN = 0.08

# Clearance the threshold must keep below the resting level. This is the pass-10 measurement turned
# into a rule: on a real 20 s trace an open hand read a median 0.915 but dipped to 0.853, so a
# threshold within ~0.1 of the resting level is a threshold an open hand will cross on its own.
OPEN_HAND_MARGIN = 0.15


@dataclass(frozen=True)
class Observation:
    """One frame, as the pipeline sees it.

    `anchor` is the pointer anchor in normalized frame coordinates and `pinch_index` the
    thumb-to-index distance in multiples of hand scale — both already computed for telemetry, so
    measuring costs the pipeline nothing beyond appending to a list.
    """

    anchor: tuple[float, float] | None
    pinch_index: float | None
    detected: bool


@dataclass(frozen=True)
class CalibrationResult:
    """What the UI renders: progress while sampling, then a measurement and a verdict."""

    step: Step
    state: State
    samples: int
    seconds_remaining: float
    """How long this step runs in total. Sent so a client can draw a progress bar without keeping
    its own copy of `STEP_SECONDS` — a copy that would go wrong the day a duration is retuned."""
    seconds_total: float = 0.0
    measurement: dict[str, Any] | None = None
    """A `set_settings` patch, or None when the measurement did not support one."""
    suggestion: dict[str, dict[str, float]] | None = None
    reason: str | None = None

    def to_message(self) -> dict[str, Any]:
        return {
            "type": "calibration",
            "step": self.step,
            "state": self.state,
            "samples": self.samples,
            "secondsRemaining": round(self.seconds_remaining, 2),
            "secondsTotal": round(self.seconds_total, 2),
            "measurement": self.measurement,
            "suggestion": self.suggestion,
            "reason": self.reason,
        }


class CalibrationSession:
    """One measurement, from the moment the user pressed the button to the verdict.

    Single use. The caller feeds every frame — including the ones with no hand, which is how the
    lost-hand timer runs — and stops when :attr:`finished` goes true.
    """

    def __init__(self, step: str, *, settings: EngineSettings, now: float) -> None:
        # Takes a plain `str` and validates here rather than trusting the annotation: the value
        # arrives off the wire, where a type hint is a wish.
        if step not in STEP_SECONDS:
            raise ValueError(f"Unknown calibration step {step!r}. Known: {', '.join(STEP_SECONDS)}")

        self.step: Step = cast(Step, step)
        self._settings = settings
        self._duration = STEP_SECONDS[step]
        self._started_at = now
        self._now = now

        self._anchors: list[tuple[float, float]] = []
        self._pinches: list[float] = []
        self._lost_since: float | None = None
        self._failure: str | None = None
        self._done = False

    # ------------------------------------------------------------------ sampling

    @property
    def finished(self) -> bool:
        return self._done

    def cancel(self) -> None:
        """End the session without a verdict — the client navigated away or tracking stopped."""
        self._done = True
        self._failure = self._failure or "Measurement cancelled."

    def observe(self, observation: Observation, *, now: float) -> None:
        if self._done:
            return
        self._now = now

        if observation.detected:
            self._lost_since = None
            if observation.anchor is not None:
                self._anchors.append(observation.anchor)
            if observation.pinch_index is not None:
                self._pinches.append(observation.pinch_index)
        else:
            if self._lost_since is None:
                self._lost_since = now
            elif now - self._lost_since >= LOST_TOLERANCE_SECONDS:
                # Ended here rather than at the end of the countdown: the user is standing in front
                # of the camera waiting, and a session that can no longer succeed should say so.
                self._fail("Lost sight of the hand. Keep it in view and try again.")
                return

        if now - self._started_at >= self._duration:
            self._finish()

    def _fail(self, reason: str) -> None:
        self._failure = reason
        self._done = True

    def _finish(self) -> None:
        self._done = True

    # ------------------------------------------------------------------- verdict

    def _report(
        self, state: State, samples: int, *, remaining: float = 0.0, **fields: Any
    ) -> CalibrationResult:
        """Every result carries the step's total length, so the caller never has to remember to."""
        return CalibrationResult(
            self.step,
            state,
            samples,
            remaining,
            seconds_total=self._duration,
            **fields,
        )

    def result(self) -> CalibrationResult:
        elapsed = self._now - self._started_at
        remaining = max(0.0, self._duration - elapsed)
        samples = len(self._anchors) if self.step != "pinch" else len(self._pinches)

        if not self._done:
            return self._report("sampling", samples, remaining=remaining)

        if self._failure is not None:
            return self._report("failed", samples, reason=self._failure)

        if samples < MIN_SAMPLES:
            return self._report(
                "failed",
                samples,
                reason=(
                    f"Only {samples} usable frames — not enough to measure anything. Check that "
                    "the hand is lit and fully in frame."
                ),
            )

        if self.step == "neutral":
            return self._neutral(samples)
        if self.step == "reach":
            return self._reach(samples)
        return self._pinch(samples)

    def _neutral(self, samples: int) -> CalibrationResult:
        """Where the hand rests, as the centre of the active area.

        The median of each axis independently. A hand told to hold still drifts, and one
        mis-detected frame can put the anchor anywhere in the picture; a mean would carry both
        into the answer.
        """
        raw_x = _percentile([point[0] for point in self._anchors], 0.5)
        raw_y = _percentile([point[1] for point in self._anchors], 0.5)

        return self._report(
            "done",
            samples,
            measurement={"centerX": round(raw_x, 4), "centerY": round(raw_y, 4)},
            suggestion={
                "cursor": {
                    "centerX": _clamped("cursor", "centerX", raw_x),
                    "centerY": _clamped("cursor", "centerY", raw_y),
                }
            },
        )

    def _reach(self, samples: int) -> CalibrationResult:
        """How wide the active area has to be to cover the user's comfortable reach.

        Measured as a distance from the centre the previous step established, then doubled: the
        region is symmetric, so covering a reach of `d` on the further side takes a width of `2d`.
        Sizing only — moving the region is the `neutral` step's job, and having two steps write the
        same field would mean the second silently undid the first.

        Vertical reach is reported but not acted on. Height is derived from width and the two
        aspect ratios, so it is not independently settable; the number is here so a user whose
        vertical reach is much shorter can see that and trim the sensitivity by hand.
        """
        centre = self._settings.cursor.center_x
        horizontal = [point[0] for point in self._anchors]
        vertical = [point[1] for point in self._anchors]

        # Percentiles, not the extremes: one frame at the edge of the picture would otherwise size
        # the whole active area and halve the sensitivity.
        low = _percentile(horizontal, 0.02)
        high = _percentile(horizontal, 0.98)
        reach = max(abs(low - centre), abs(high - centre))

        return self._report(
            "done",
            samples,
            measurement={
                "spanX": round(high - low, 4),
                "spanY": round(
                    _percentile(vertical, 0.98) - _percentile(vertical, 0.02), 4
                ),
                "reach": round(reach, 4),
            },
            suggestion={"cursor": {"coverage": _clamped("cursor", "coverage", 2.0 * reach)}},
        )

    def _pinch(self, samples: int) -> CalibrationResult:
        """The threshold at which this user's pinch counts as closed.

        The resting level is the 90th percentile: the hand is open for most of the window, and
        pinching is brief. Every contiguous run that dips `ATTEMPT_DEPTH` below it is one attempt,
        and the threshold is keyed on the **worst** of them — a threshold tuned to the deepest
        pinch would miss the other two, which is exactly the bug this step exists to fix.

        The threshold then sits a *fraction of the measured gap* above that worst attempt, not a
        fixed offset. See `PINCH_GAP_FRACTION`: a deliberate pinch is always a clean one, so the
        margin has to cover the messy ones that never appear in the sample.
        """
        resting = _percentile(self._pinches, 0.9)
        minima = _attempt_minima(self._pinches, resting - ATTEMPT_DEPTH)

        measurement: dict[str, Any] = {
            "restingLevel": round(resting, 4),
            "attempts": len(minima),
            "worstPinch": round(max(minima), 4) if minima else None,
            "bestPinch": round(min(minima), 4) if minima else None,
        }

        if len(minima) < MIN_PINCH_ATTEMPTS:
            return self._report(
                "failed",
                samples,
                measurement=measurement,
                reason=(
                    f"Saw {len(minima)} clear pinch(es); {MIN_PINCH_ATTEMPTS} are needed before "
                    "a threshold means anything. Touch thumb and index finger together firmly, "
                    "then open the hand fully between attempts."
                ),
            )

        worst = max(minima)
        close = worst + max(MIN_PINCH_MARGIN, PINCH_GAP_FRACTION * (resting - worst))
        if close > resting - OPEN_HAND_MARGIN:
            # The refusal that matters. A threshold this close to the resting level fires on an
            # open hand, and a phantom click is a worse failure than the missed click this step is
            # here to fix — see the 0.853 measurement in progress.md.
            return self._report(
                "failed",
                samples,
                measurement=measurement,
                reason=(
                    f"The deepest pinch reached {max(minima):.2f} but an open hand rests at "
                    f"{resting:.2f} — too close to tell apart, so any threshold here would fire "
                    "on an open hand. Open the hand fully between attempts and press harder."
                ),
            )

        # The hysteresis band is the user's, carried across unchanged. It is the only thing
        # standing between a hand resting near the threshold and a burst of phantom clicks, so it
        # is not something a measurement of pinch depth gets to redefine.
        band = self._settings.gesture.pinch_open - self._settings.gesture.pinch_close
        return self._report(
            "done",
            samples,
            measurement=measurement,
            suggestion={
                "gesture": {
                    "pinchClose": _clamped("gesture", "pinchClose", close),
                    "pinchOpen": _clamped("gesture", "pinchOpen", close + band),
                }
            },
        )


class CalibrationRunner:
    """Holds at most one session, safely, for a telemetry source to feed.

    Two threads meet here: the server thread starts and cancels, the pipeline thread observes.
    Sources own one of these and delegate to it, so the live and synthetic pipelines cannot drift
    apart in how a measurement behaves — the synthetic one exists precisely so the Calibration
    screen is exercisable without a webcam.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: CalibrationSession | None = None
        self._result: CalibrationResult | None = None

    def start(self, step: str, *, settings: EngineSettings, now: float) -> CalibrationResult:
        """Begin a measurement, replacing any session already running.

        Replacing rather than refusing: the previous one can only be there because the user
        restarted the step, and making them wait out a countdown they abandoned would be worse
        than dropping it.
        """
        session = CalibrationSession(step, settings=settings, now=now)
        with self._lock:
            self._session = session
            self._result = session.result()
        return self._result

    def cancel(self) -> None:
        with self._lock:
            if self._session is None:
                return
            self._session.cancel()
            self._result = self._session.result()
            self._session = None

    def observe(self, observation: Observation, *, now: float) -> None:
        """Feed one frame. Cheap and lock-guarded — this runs on the pipeline thread."""
        with self._lock:
            session = self._session
            if session is None:
                return
            session.observe(observation, now=now)
            self._result = session.result()
            if session.finished:
                self._session = None

    def result(self) -> CalibrationResult | None:
        """The latest state, or None if no measurement has been taken this session."""
        with self._lock:
            return self._result


def _attempt_minima(values: Sequence[float], threshold: float) -> list[float]:
    """Minimum of every run that stays below `threshold` for long enough to be deliberate."""
    minima: list[float] = []
    run: list[float] = []
    for value in values:
        if value < threshold:
            run.append(value)
            continue
        if len(run) >= MIN_ATTEMPT_SAMPLES:
            minima.append(min(run))
        run = []
    if len(run) >= MIN_ATTEMPT_SAMPLES:
        minima.append(min(run))
    return minima


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Linear-interpolated percentile. `fraction` 0.5 is the median."""
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _clamped(section: str, wire: str, value: float) -> float:
    """Fit a derived value into the range the engine accepts.

    Clamping a *suggestion* is safe in a way that clamping a user's typed value would not be: the
    raw measurement is reported alongside it, so a hand parked at the edge of the frame shows up as
    a number that was pulled in rather than as a mysterious refusal at the moment the user accepts.
    """
    bounds = bounds_for(section, wire)
    assert bounds is not None, f"{section}.{wire} is a boolean and cannot be measured"
    low, high = bounds
    return round(max(low, min(high, value)), 4)
