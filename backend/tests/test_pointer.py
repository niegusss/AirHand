"""Pointer stage.

Two groups of properties. The first is about *quality* — the anchor and the filter must actually
reduce what reaches the screen — and is asserted statistically, because a single frame proves
nothing about noise.

The second is about **safety**, and matters more: the pointer holds state across gaps in detection,
and state that outlives the hand is how a drag ends up stuck on a machine whose owner has already
walked away. The dropout tests exist to pin down that the grace window buys time for the filter and
never for the output.
"""

from __future__ import annotations

import random
import statistics

import pytest

from airhand.gestures.features import INDEX_MCP, palm_center
from airhand.pointer import PointerConfig, PointerTracker

from tests.fixtures.hands import POINTING, make_hand

FPS = 30.0


def _noisy(landmarks, rng: random.Random, sigma: float = 0.004):
    """The same hand seen through a noisy detector — independent jitter per landmark."""
    return [[x + rng.gauss(0, sigma), y + rng.gauss(0, sigma), z] for x, y, z in landmarks]


def _tracker(**overrides) -> PointerTracker:
    return PointerTracker(PointerConfig(**overrides))


# ------------------------------------------------------------------------- anchor


def test_palm_centre_is_steadier_than_a_single_landmark() -> None:
    """The whole reason the cursor stopped hanging off `landmarks[5]`.

    Five rigid palm points averaged; whatever part of the detector's noise is independent between
    landmarks is attenuated for free, with no filter and therefore no lag.
    """
    rng = random.Random(7)
    hand = make_hand(POINTING, pinch_index=0.9)

    single_x: list[float] = []
    palm_x: list[float] = []
    for _ in range(400):
        noisy = _noisy(hand, rng)
        single_x.append(noisy[INDEX_MCP][0])
        palm_x.append(palm_center(noisy)[0])

    assert statistics.pstdev(palm_x) < statistics.pstdev(single_x) / 1.5


def test_anchor_tracks_the_hand_rather_than_the_fingers() -> None:
    """Pinching must not move the anchor — the click would drag the cursor off its target."""
    open_hand = palm_center(make_hand(POINTING, pinch_index=0.9))
    pinched = palm_center(make_hand(POINTING, pinch_index=0.05))

    assert open_hand[0] == pytest.approx(pinched[0], abs=1e-9)
    assert open_hand[1] == pytest.approx(pinched[1], abs=1e-9)


# ------------------------------------------------------------------------ filter


def test_smoothing_reduces_jitter_at_rest() -> None:
    rng = random.Random(11)
    hand = make_hand(POINTING, pinch_index=0.9)
    tracker = _tracker()

    raw: list[float] = []
    smoothed: list[float] = []
    for frame in range(300):
        noisy = _noisy(hand, rng)
        position = tracker.update(noisy, now=frame / FPS)
        assert position is not None
        raw.append(palm_center(noisy)[0])
        smoothed.append(position[0])

    # Skip the warm-up: the first samples pass through by design.
    #
    # Halving, not a tighter ratio. How much the filter removes is a property of the tuning, and
    # the tuning moves when someone records a better trace — this test has to keep asserting that
    # smoothing *works* without failing every time it is adjusted. It was pinned at a third until
    # 2026-08-08, purely because that matched the value shipped at the time, and it duly broke on
    # the first real re-tune. The guarantee is the floor; the measured ratio lives in progress.md.
    assert statistics.pstdev(smoothed[60:]) < statistics.pstdev(raw[60:]) / 2


def test_sustained_motion_is_not_left_far_behind() -> None:
    """Heavy smoothing at rest must not become heavy lag while moving."""
    tracker = _tracker()

    position = None
    for frame in range(90):
        hand = make_hand(POINTING, center=(0.3 + frame * 0.004, 0.6), pinch_index=0.9)
        position = tracker.update(hand, now=frame / FPS)
        target = palm_center(hand)

    assert position is not None
    assert abs(position[0] - target[0]) < 0.02


def test_frame_rate_independence() -> None:
    """The same motion over the same wall-clock time must land in the same place at 30 and 60 fps.

    "Runs on any webcam" is only true if the pointer feels the same on a fast camera and a slow one.
    """

    def run(fps: int) -> float:
        tracker = _tracker()
        steps = int(1.5 * fps)
        position = (0.0, 0.0)
        for step in range(steps):
            hand = make_hand(POINTING, center=(0.3 + 0.3 * (step + 1) / steps, 0.6), pinch_index=0.9)
            result = tracker.update(hand, now=step / fps)
            assert result is not None
            position = result
        return position[0]

    assert run(30) == pytest.approx(run(60), abs=0.005)


# -------------------------------------------------------------------------- hold


def test_hold_freezes_the_cursor_while_a_pinch_is_undecided() -> None:
    """The fix for "the click landed next to what I was pointing at"."""
    tracker = _tracker()

    for frame in range(30):
        tracker.update(make_hand(POINTING, pinch_index=0.9), now=frame / FPS)
    frozen = tracker.position
    assert frozen is not None

    # The hand drifts while the pinch closes — as hands do — and the pointer must not follow.
    for frame in range(30, 42):
        hand = make_hand(POINTING, center=(0.5 + (frame - 30) * 0.01, 0.65), pinch_index=0.15)
        assert tracker.update(hand, hold=True, now=frame / FPS) == frozen


def test_releasing_a_hold_eases_out_instead_of_snapping() -> None:
    """A drag begins by moving, so the frame after a hold is where a snap would be visible."""
    tracker = _tracker()

    for frame in range(30):
        tracker.update(make_hand(POINTING, center=(0.5, 0.65), pinch_index=0.9), now=frame / FPS)
    frozen = tracker.position
    assert frozen is not None

    moved = make_hand(POINTING, center=(0.65, 0.65), pinch_index=0.15)
    for frame in range(30, 42):
        tracker.update(moved, hold=True, now=frame / FPS)

    resumed = tracker.update(moved, hold=False, now=42 / FPS)
    assert resumed is not None

    target = palm_center(moved)[0]
    assert frozen[0] < resumed[0] < target, "the pointer must move toward the hand, not jump to it"


def test_hold_is_ignored_before_there_is_anything_to_hold() -> None:
    """A hand whose very first frame is already pinched has no frozen position to return."""
    tracker = _tracker()
    hand = make_hand(POINTING, pinch_index=0.15)
    assert tracker.update(hand, hold=True, now=0.0) is not None


def test_hold_can_be_switched_off() -> None:
    tracker = _tracker(hold_on_pinch=False)

    for frame in range(30):
        tracker.update(make_hand(POINTING, center=(0.5, 0.65), pinch_index=0.9), now=frame / FPS)
    before = tracker.position
    assert before is not None

    for frame in range(30, 45):
        tracker.update(
            make_hand(POINTING, center=(0.65, 0.65), pinch_index=0.15), hold=True, now=frame / FPS
        )

    assert tracker.position is not None
    assert tracker.position[0] != before[0]


# ---------------------------------------------------------------------- dropouts


def test_a_lost_hand_reports_nothing_immediately_despite_the_grace_window() -> None:
    """The safety case. The grace window buys time for the *filter state*, never for the output.

    Returning a stale anchor here would keep a drag alive on a hand that has left the frame, and
    a mouse button the user cannot release is the worst failure this pipeline can produce.
    """
    tracker = _tracker(dropout_grace_seconds=0.5)

    for frame in range(30):
        tracker.update(make_hand(POINTING, pinch_index=0.9), now=frame / FPS)
    assert tracker.position is not None

    assert tracker.update(None, now=30 / FPS) is None
    assert tracker.update(None, hold=True, now=31 / FPS) is None


def test_a_brief_dropout_keeps_the_smoothing_state() -> None:
    """One or two missed detections must not hand the cursor a burst of unsmoothed samples."""
    tracker = _tracker(dropout_grace_seconds=0.2)

    settled = make_hand(POINTING, center=(0.5, 0.65), pinch_index=0.9)
    for frame in range(40):
        tracker.update(settled, now=frame / FPS)
    before = tracker.position
    assert before is not None

    tracker.update(None, now=40 / FPS)
    tracker.update(None, now=41 / FPS)

    elsewhere = make_hand(POINTING, center=(0.7, 0.65), pinch_index=0.9)
    resumed = tracker.update(elsewhere, now=42 / FPS)

    assert resumed is not None
    assert resumed[0] != pytest.approx(palm_center(elsewhere)[0]), (
        "state survived the gap, so the first frame back must still be smoothed"
    )


def test_a_long_dropout_starts_over() -> None:
    """Beyond the grace window the old position is not evidence about the new one."""
    tracker = _tracker(dropout_grace_seconds=0.2)

    settled = make_hand(POINTING, center=(0.5, 0.65), pinch_index=0.9)
    for frame in range(40):
        tracker.update(settled, now=frame / FPS)

    for frame in range(40, 60):  # 0.67 s of nothing
        tracker.update(None, now=frame / FPS)

    elsewhere = make_hand(POINTING, center=(0.7, 0.65), pinch_index=0.9)
    resumed = tracker.update(elsewhere, now=60 / FPS)

    assert resumed == pytest.approx(palm_center(elsewhere))


def test_reset_clears_everything() -> None:
    tracker = _tracker()
    for frame in range(30):
        tracker.update(make_hand(POINTING, pinch_index=0.9), now=frame / FPS)

    tracker.reset()
    assert tracker.position is None

    elsewhere = make_hand(POINTING, center=(0.2, 0.3), pinch_index=0.9)
    assert tracker.update(elsewhere, now=2.0) == pytest.approx(palm_center(elsewhere))


def test_a_truncated_landmark_list_is_treated_as_no_hand() -> None:
    assert _tracker().update([[0.5, 0.5, 0.0]] * 5, now=0.0) is None
