"""Pointer stage — where the cursor should be, given a hand.

Sits between the Gesture Engine and the Cursor Engine and owns everything about *pointing* that is
not geometry: which part of the hand the cursor follows, how hard that position is smoothed, and
when it deliberately stops following.

Deliberately **not** in `cursor/`. That package is the only code that writes to the OS and every
rule in it is a safety rule, so it stays as small as it can be. This stage touches nothing, runs on
no thread of its own and takes its clock as an argument, which is what makes it directly testable —
the same reasoning that put `preview.py` in its own module.

Deliberately **not** in `filters/` either. `LandmarkFilter` there smooths the whole hand for the
Gesture Engine and the UI overlay, and both want *low* lag: a late pinch is a late click. Pointing
wants the opposite — heavy smoothing, because the mapping amplifies landmark noise by the ratio of
screen width to active area, roughly 2700 px per normalized unit at the default coverage. One
filter cannot serve both, and the compromise setting served neither.

So the pointer runs its own filter, in **parallel** on the raw landmarks rather than in series
after the landmark filter. Chaining two filters would add their lag together for no benefit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .filters import OneEuroConfig, Vec2Filter
from .gestures.features import PALM_LANDMARKS, palm_center

# The pointer only reads the palm, so this is all it needs to be present.
LANDMARKS_REQUIRED = max(PALM_LANDMARKS) + 1


@dataclass(frozen=True)
class PointerConfig:
    """Tuning for the cursor position specifically.

    `min_cutoff` and `beta` are the One Euro knobs, in Hz, applied to normalized frame coordinates.

    **Measured, not chosen.** From a real trace on the dev webcam (`tools/bench_pointer.py`):
    at-rest error is flat at its floor of 1.3 px RMS across `min_cutoff` 0.8–2.0 and only rises at
    3.0, while lag falls the whole way. So the right pick is the *lightest* smoothing that still
    holds the floor — heavier buys nothing and costs lag. 1.5 / 10.0 gives 1.3 px RMS, 2.2 px p95
    and 10.2 px of lag; the earlier 0.8 / 6.0, picked off a synthetic trace, matched it on
    steadiness and trailed the hand half again as far.

    Landing this close to the landmark filter's own setting (1.5 / 8.0) is a real finding: on this
    camera almost all of the pointer's steadiness comes from the palm-centroid anchor, not from
    tuning the filter differently. The stage still earns its place — it owns the click hold and the
    dropout rule, and the two filters are free to diverge — but the tuning gap is small here.

    Both are exposed on the CLI (`--pointer-min-cutoff`, `--pointer-beta`) so they can be re-tuned
    against a trace from another camera without a rebuild. The Calibration screen will own them
    eventually, the same way it will own `--cursor-coverage`.
    """

    min_cutoff: float = 1.5
    beta: float = 10.0
    d_cutoff: float = 1.0
    """Hold the cursor still while a pinch is closed but unresolved, so a click lands where the
    user aimed rather than where the hand drifted while closing."""
    hold_on_pinch: bool = True
    """How long a detection dropout may last before the smoothing state is thrown away.

    Only the *state* survives the gap — never the output. See :meth:`PointerTracker.update`.
    """
    dropout_grace_seconds: float = 0.2

    def __post_init__(self) -> None:
        # These arrive from a client over the wire and end up steering OS input, so they are
        # checked at construction rather than trusted. A zero cutoff divides by zero inside the
        # filter; a negative one would run it backwards.
        if self.min_cutoff <= 0.0:
            raise ValueError(f"min_cutoff must be positive, got {self.min_cutoff}")
        if self.d_cutoff <= 0.0:
            raise ValueError(f"d_cutoff must be positive, got {self.d_cutoff}")
        if self.beta < 0.0:
            raise ValueError(f"beta must not be negative, got {self.beta}")
        if self.dropout_grace_seconds < 0.0:
            raise ValueError(
                f"dropout_grace_seconds must not be negative, got {self.dropout_grace_seconds}"
            )


class PointerTracker:
    """Turns a stream of hands into the position the cursor should occupy.

    Stateful and single-threaded: one instance per pipeline run, driven from the pipeline thread.
    """

    def __init__(self, config: PointerConfig | None = None) -> None:
        self._config = config or PointerConfig()
        self._filter = Vec2Filter(
            OneEuroConfig(
                min_cutoff=self._config.min_cutoff,
                beta=self._config.beta,
                d_cutoff=self._config.d_cutoff,
            )
        )
        self._position: tuple[float, float] | None = None
        self._last_frame_at: float | None = None
        self._lost_since: float | None = None

    @property
    def config(self) -> PointerConfig:
        return self._config

    @property
    def position(self) -> tuple[float, float] | None:
        """Last emitted position, or None if the pointer has nothing to report."""
        return self._position

    def configure(self, config: PointerConfig) -> None:
        """Swap the tuning while running, for a calibration slider being dragged.

        The filter is rebuilt — its coefficients are baked in at construction — but the **last
        emitted position is kept**. Dropping it would teleport the cursor to the raw hand position
        on the next frame, which is a startling thing to happen while someone is adjusting a
        smoothing slider to make the cursor calmer.
        """
        self._config = config
        self._filter = Vec2Filter(
            OneEuroConfig(
                min_cutoff=config.min_cutoff, beta=config.beta, d_cutoff=config.d_cutoff
            )
        )
        # The rebuilt filter has no history, so the next sample passes through and the one after
        # seeds it. Two frames of reduced smoothing at 30 fps is not perceptible; a jump is.
        self._last_frame_at = None

    def reset(self) -> None:
        self._clear()
        self._lost_since = None

    def update(
        self,
        landmarks: Sequence[Sequence[float]] | None,
        *,
        hold: bool = False,
        now: float,
    ) -> tuple[float, float] | None:
        """One frame. Returns the cursor anchor in normalized frame coordinates, or None.

        Pass the **raw** landmarks — smoothing them twice only adds lag.

        `hold` comes from :attr:`~airhand.gestures.GestureUpdate.pointer_hold`.

        **No hand means None, immediately.** The dropout grace below buys time for the *smoothing
        state*, never for the output: reporting a stale anchor for even a fraction of a second
        would keep a drag alive on a hand that is no longer there, and a drag the user cannot end
        is the worst thing this pipeline can produce.
        """
        if landmarks is None or len(landmarks) < LANDMARKS_REQUIRED:
            self._on_lost(now)
            return None

        self._lost_since = None

        # Measured from the previous *frame*, not from the last sample fed to the filter. During a
        # hold no sample is fed, and using the hold's full duration as dt would make the filter's
        # alpha approach 1 — the pointer would snap to the hand the instant the hold ended, which
        # is exactly what the hold exists to prevent. A dropout is the opposite case: time really
        # did pass and the position really is stale, so the larger dt there is correct.
        dt = 0.0 if self._last_frame_at is None else now - self._last_frame_at
        self._last_frame_at = now

        if hold and self._config.hold_on_pinch and self._position is not None:
            # The filter is left untouched, so when the hold lifts it resumes from the frozen point
            # and eases toward the hand rather than jumping. A drag starting here therefore starts
            # from the point that was clicked, which is what a drag means.
            return self._position

        centre = palm_center(landmarks)
        self._position = self._filter.filter(centre[0], centre[1], dt)
        return self._position

    def _on_lost(self, now: float) -> None:
        if self._lost_since is None:
            self._lost_since = now
        if now - self._lost_since >= self._config.dropout_grace_seconds:
            self._clear()

    def _clear(self) -> None:
        self._filter.reset()
        self._position = None
        self._last_frame_at = None
