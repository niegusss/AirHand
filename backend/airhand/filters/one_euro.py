"""One Euro filter — adaptive smoothing for noisy interactive signals.

Chosen over a fixed exponential moving average because a fixed EMA forces an unwinnable choice:
smooth enough to kill jitter at rest means visibly laggy while moving, and responsive enough to
track fast motion means the cursor trembles when the hand is still. One Euro adapts its cutoff to
the observed speed, so it is heavy at rest and light in motion.

Every parameter is a frequency in Hz and every update takes a real `dt`, so behaviour is identical
at 30 and 60 fps — which is what "must work on any webcam" requires. A frame-counted filter would
smooth twice as hard on a 60 fps camera as on a 30 fps one.

Reference: Casiez, Roussel & Vogel, "1€ Filter: A Simple Speed-based Low-pass Filter for Noisy
Input in Interactive Systems" (CHI 2012).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _alpha(cutoff_hz: float, dt: float) -> float:
    """Smoothing factor for a first-order low-pass with the given cutoff and timestep."""
    tau = 1.0 / (2.0 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + tau / dt)


class _LowPass:
    """First-order low-pass with an externally supplied alpha."""

    def __init__(self) -> None:
        self._value: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    def reset(self) -> None:
        self._value = None

    def filter(self, sample: float, alpha: float) -> float:
        if self._value is None:
            self._value = sample
        else:
            self._value = alpha * sample + (1.0 - alpha) * self._value
        return self._value


@dataclass(frozen=True)
class OneEuroConfig:
    """Tuning knobs, all in Hz except `beta`.

    `min_cutoff` sets how still the signal is at rest — lower is smoother but laggier.
    `beta` sets how quickly the filter opens up as speed rises — higher cuts lag during fast
    motion at the cost of letting more jitter through.
    `d_cutoff` smooths the speed estimate itself, so a single noisy frame cannot slam the filter
    wide open.

    **`beta` looks large compared to the values quoted in the literature, and it must be.** Those
    assume coordinates in pixels, where hand speeds are in the hundreds of units per second. Ours
    are normalized 0..1, so a brisk drag is well under 1.0 unit/s. With a pixel-tuned beta the
    adaptive term contributes almost nothing and the filter degenerates into a fixed low-pass —
    measured at ~130 ms of lag during a steady drag, which is unusable for a cursor.

    Current values give roughly 100 ms of smoothing at rest (kills jitter) and ~25 ms during a
    0.6 unit/s drag (barely perceptible).
    """

    min_cutoff: float = 1.5
    beta: float = 8.0
    d_cutoff: float = 1.0


class OneEuroFilter:
    """Scalar One Euro filter."""

    def __init__(self, config: OneEuroConfig | None = None) -> None:
        self._config = config or OneEuroConfig()
        self._x = _LowPass()
        self._dx = _LowPass()
        self._last_value: float | None = None

    def reset(self) -> None:
        self._x.reset()
        self._dx.reset()
        self._last_value = None

    def filter(self, sample: float, dt: float) -> float:
        if dt <= 0.0:
            # A non-positive timestep would divide by zero in the alpha computation. Passing the
            # sample through unchanged is the only honest answer: no time elapsed, no new
            # information about velocity.
            return self._x.value if self._x.value is not None else sample

        config = self._config

        # Derivative of the raw signal, itself low-passed so one noisy frame cannot spike it.
        derivative = 0.0 if self._last_value is None else (sample - self._last_value) / dt
        self._last_value = sample
        smoothed_derivative = self._dx.filter(derivative, _alpha(config.d_cutoff, dt))

        # The adaptive part: cutoff rises with speed, so fast motion is barely filtered.
        cutoff = config.min_cutoff + config.beta * abs(smoothed_derivative)
        return self._x.filter(sample, _alpha(cutoff, dt))


class Vec2Filter:
    """Two independent One Euro filters, for a point in the image plane."""

    def __init__(self, config: OneEuroConfig | None = None) -> None:
        self._x = OneEuroFilter(config)
        self._y = OneEuroFilter(config)

    def reset(self) -> None:
        self._x.reset()
        self._y.reset()

    def filter(self, x: float, y: float, dt: float) -> tuple[float, float]:
        return self._x.filter(x, dt), self._y.filter(y, dt)


class LandmarkFilter:
    """Smooths a whole hand: one Vec2Filter per landmark.

    Filtering every landmark (rather than only the cursor point) matters because the Gesture
    Engine reads distances between landmarks. Unfiltered jitter there produces phantom pinch
    transitions, which is exactly what the hysteresis in the engine should not have to absorb.
    """

    def __init__(self, count: int, config: OneEuroConfig | None = None) -> None:
        self._filters = [Vec2Filter(config) for _ in range(count)]

    def reset(self) -> None:
        for filter_ in self._filters:
            filter_.reset()

    def filter(self, landmarks: list[list[float]], dt: float) -> list[list[float]]:
        # A different landmark count means a different hand model; filtering it against stale
        # per-landmark state would silently mix two coordinate systems.
        if len(landmarks) != len(self._filters):
            self.reset()
            return landmarks

        smoothed: list[list[float]] = []
        for index, point in enumerate(landmarks):
            x, y = self._filters[index].filter(point[0], point[1], dt)
            # z is passed through: MediaPipe's relative depth is too noisy to be worth filtering
            # and nothing downstream uses it yet.
            smoothed.append([x, y, point[2] if len(point) > 2 else 0.0])
        return smoothed
