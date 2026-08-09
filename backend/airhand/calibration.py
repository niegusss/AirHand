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
STEP_SECONDS: dict[Step, float] = {"neutral": 3.0, "reach": 6.0, "pinch": 12.0}

# The pinch step runs in two phases: deliberate pinches, then a closed hand. The second is not a
# formality — see PINCH_GAP_FRACTION for what it buys and what its absence cost.
PINCH_PHASE_SECONDS = 8.0

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

# Where the threshold sits between a firm pinch and the nearest thing that could be mistaken for
# one.
#
# **The ceiling is the closed hand, not the resting hand.** The first version measured the gap up
# to the *resting* level and put the threshold a third of the way across it. On a real hand
# (2026-08-09: worst pinch 0.10, resting 1.16) that produced **0.4695** — and a fist on that same
# hand reaches **0.195**. The threshold was placed most of the way up a gap that is mostly empty
# space, far above the only pose that can actually be confused with a pinch. Measured consequences,
# all three observed in one recording: closing the hand fired five drags, five of eight left clicks
# became drags, and every right click became nothing at all.
#
# Anchoring to the fist floor makes the gap the real one. A half rather than a third, because both
# ends are now genuine measurements of confusable poses, so the midpoint is the honest place to
# stand — there is no reason to lean toward either.
PINCH_GAP_FRACTION = 0.5

# Clearance kept under the observed fist floor, so the threshold is not sitting exactly on the
# deepest a closed hand was seen to reach.
FIST_MARGIN = 0.03

# How far the fingers must actually travel below the resting hand for the gesture to be a pinch at
# all, in multiples of hand scale.
#
# This is the pass-10 refusal restated as the thing it was always really measuring. The first
# version expressed it through margins on the threshold, which made it depend on how wide the gap
# happened to be — and a hand whose fist sits close to its pinch has a genuinely narrow gap while
# still pinching perfectly well, so a margin rule refuses it for the wrong reason.
#
# Travel is the honest quantity. Measured: a firm pinch travels ~1.06 (2026-08-09, worst attempt
# 0.10 against a hand resting at 1.16), while per-frame noise on an open hand covers about 0.06
# (pass 10: median 0.915, dipping to 0.853). 0.30 sits well clear of both.
MIN_PINCH_TRAVEL = 0.30

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
    """Which instruction to show right now, for steps that ask for more than one thing. None for
    steps that ask for a single pose. Added in protocol 1.8.0."""
    phase: str | None = None
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
            "phase": self.phase,
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
        """Thumb-to-index readings from the closed-hand phase, kept apart from the pinch ones.

        Split by the clock rather than found in the signal: a fist and a pinch overlap in this
        measurement — that overlap is the entire problem — so nothing in the numbers themselves can
        tell them apart. The prompt is what labels them.
        """
        self._fists: list[float] = []
        self._lost_since: float | None = None
        self._failure: str | None = None
        self._done = False

    # ------------------------------------------------------------------ sampling

    @property
    def finished(self) -> bool:
        return self._done

    @property
    def phase(self) -> str | None:
        """Which instruction the user should be following right now.

        Only the pinch step has phases. It asks for two different things and the second one — a
        closed hand — is what bounds the threshold from above; a step that only ever showed
        "pinch three times" would collect no evidence about it.
        """
        if self.step != "pinch":
            return None
        return "pinch" if self._now - self._started_at < PINCH_PHASE_SECONDS else "fist"

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
                if self.phase == "fist":
                    self._fists.append(observation.pinch_index)
                else:
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
            phase=self.phase if state == "sampling" else None,
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

        # The p02 rather than the minimum, for the same reason nothing else here is a min: one
        # frame of a badly-estimated thumb would otherwise define the ceiling for the whole hand.
        fist_floor = _percentile(self._fists, 0.02) if self._fists else None
        measurement["fistFloor"] = round(fist_floor, 4) if fist_floor is not None else None

        # Two ceilings, and the threshold has to clear both. The open hand was the only one until
        # 2026-08-09; it is far away and almost never binding. The closed hand is the near one, and
        # it is what a threshold actually collides with.
        ceiling = resting - OPEN_HAND_MARGIN
        if fist_floor is not None:
            ceiling = min(ceiling, fist_floor - FIST_MARGIN)

        # Two refusals, and both matter for the same reason: a phantom click is a worse failure
        # than the missed click this step exists to fix. A click that did not land can be repeated;
        # one that landed somewhere the user was not looking cannot be taken back.
        if resting - worst < MIN_PINCH_TRAVEL:
            return self._report(
                "failed",
                samples,
                measurement=measurement,
                reason=(
                    f"The weakest pinch only reached {worst:.2f} against an open hand at "
                    f"{resting:.2f} — barely a move, so any threshold here would fire on an open "
                    "hand. Press thumb and finger firmly together, and open the hand fully between "
                    "attempts."
                ),
            )

        if worst >= ceiling:
            return self._report(
                "failed",
                samples,
                measurement=measurement,
                reason=(
                    f"A closed hand reaches {fist_floor:.2f} and the weakest pinch only reached "
                    f"{worst:.2f} — too close to tell apart, so any threshold here would fire when "
                    "you simply close your hand. Pinch with the rest of the hand relaxed rather "
                    "than curled."
                ),
            )

        close = worst + PINCH_GAP_FRACTION * (ceiling - worst)

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
