"""Motion Filter.

The properties that matter are behavioural, not numeric: jitter must shrink, a real move must not
lag badly, and — critically for "any webcam" — the result must not depend on the frame rate.
"""

from __future__ import annotations

import statistics

import pytest

from airhand.filters import LandmarkFilter, OneEuroConfig, OneEuroFilter


def test_converges_to_a_constant_signal() -> None:
    filter_ = OneEuroFilter()
    value = 0.0
    for _ in range(120):
        value = filter_.filter(0.75, dt=1 / 60)
    assert value == pytest.approx(0.75, abs=1e-3)


def test_first_sample_passes_through_unchanged() -> None:
    """No history means no basis for smoothing; inventing one would add startup lag."""
    assert OneEuroFilter().filter(0.42, dt=1 / 60) == pytest.approx(0.42)


def test_reduces_jitter_around_a_stationary_point() -> None:
    noisy = [0.5 + (0.01 if index % 2 == 0 else -0.01) for index in range(120)]

    filter_ = OneEuroFilter()
    smoothed = [filter_.filter(sample, dt=1 / 60) for sample in noisy]

    # Ignore the warm-up; the filter starts at the first sample by design.
    raw_spread = statistics.pstdev(noisy[20:])
    smoothed_spread = statistics.pstdev(smoothed[20:])
    assert smoothed_spread < raw_spread / 3


def test_tracks_sustained_motion_without_falling_far_behind() -> None:
    """Heavy smoothing at rest must not turn into heavy lag while moving — that is the whole
    reason for choosing One Euro over a fixed EMA."""
    filter_ = OneEuroFilter()
    position = 0.0
    smoothed = 0.0
    for _ in range(90):
        position += 0.01  # a brisk, steady drag
        smoothed = filter_.filter(position, dt=1 / 60)

    assert abs(position - smoothed) < 0.05


def test_frame_rate_independence() -> None:
    """The same motion over the same wall-clock time must land in the same place at 30 and 60 fps.

    A frame-counted filter fails this: it would smooth twice as hard on the faster camera.
    """
    duration = 1.5
    travel = 0.6

    def run(fps: int) -> float:
        filter_ = OneEuroFilter()
        steps = int(duration * fps)
        value = 0.0
        for step in range(steps):
            value = filter_.filter(travel * (step + 1) / steps, dt=1.0 / fps)
        return value

    assert run(30) == pytest.approx(run(60), abs=0.01)


def test_non_positive_dt_is_ignored() -> None:
    filter_ = OneEuroFilter()
    filter_.filter(0.3, dt=1 / 60)
    # A duplicate timestamp carries no new information about velocity.
    assert filter_.filter(0.9, dt=0.0) == pytest.approx(0.3)


def test_reset_clears_history() -> None:
    filter_ = OneEuroFilter()
    for _ in range(60):
        filter_.filter(0.9, dt=1 / 60)
    filter_.reset()
    assert filter_.filter(0.1, dt=1 / 60) == pytest.approx(0.1)


def test_beta_zero_is_a_plain_low_pass() -> None:
    """Sanity check on the adaptive term: with beta 0 the cutoff never opens up."""
    fixed = OneEuroFilter(OneEuroConfig(min_cutoff=1.0, beta=0.0))
    adaptive = OneEuroFilter(OneEuroConfig(min_cutoff=1.0, beta=2.0))

    fixed_value = adaptive_value = 0.0
    for step in range(60):
        target = step * 0.02
        fixed_value = fixed.filter(target, dt=1 / 60)
        adaptive_value = adaptive.filter(target, dt=1 / 60)

    # The adaptive filter lets fast motion through, so it sits closer to the moving target.
    assert adaptive_value > fixed_value


class TestLandmarkFilter:
    def test_smooths_every_landmark(self) -> None:
        filter_ = LandmarkFilter(count=3)
        for _ in range(60):
            result = filter_.filter([[0.5, 0.5, 0.0], [0.2, 0.8, 0.0], [0.9, 0.1, 0.0]], dt=1 / 60)
        assert result[0][0] == pytest.approx(0.5, abs=1e-3)
        assert result[2][1] == pytest.approx(0.1, abs=1e-3)

    def test_passes_z_through(self) -> None:
        filter_ = LandmarkFilter(count=1)
        result = filter_.filter([[0.5, 0.5, -0.03]], dt=1 / 60)
        assert result[0][2] == -0.03

    def test_mismatched_landmark_count_resets_instead_of_mixing_state(self) -> None:
        """A different count means a different hand model — filtering it against stale per-landmark
        state would silently blend two coordinate systems."""
        filter_ = LandmarkFilter(count=3)
        filter_.filter([[0.5, 0.5, 0.0]] * 3, dt=1 / 60)
        passthrough = filter_.filter([[0.1, 0.2, 0.0]] * 2, dt=1 / 60)
        assert passthrough == [[0.1, 0.2, 0.0]] * 2
