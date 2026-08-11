"""Synthetic hand geometry.

The fixture is read two ways: the gesture tests measure it, and the synthetic telemetry source
draws it on the UI's landmark overlay. It used to satisfy only the first. A pose can measure
exactly right and still be impossible to make with a hand — and since `--source synthetic` exists
so the interface can be worked on without a webcam, an impossible-looking hand undermines the one
job that source has.
"""

from __future__ import annotations

import math

import pytest

from airhand.handmodel import _MIN_THUMB_REACH
from tests.fixtures.hands import FIST, OPEN_HAND, POINTING, SCROLL_POSE, make_hand

THUMB_CHAIN = (1, 2, 3, 4)
INDEX_TIP = 8
MIDDLE_TIP = 12

POSES = [OPEN_HAND, POINTING, SCROLL_POSE, FIST]
GAPS = [0.15, 0.35, 0.55, 0.9, 1.10]


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


@pytest.mark.parametrize("gap", GAPS)
@pytest.mark.parametrize("pose", POSES)
def test_the_requested_pinch_gap_is_what_the_hand_measures(pose, gap: float) -> None:
    """The gap is the quantity the Gesture Engine thresholds on, so it is a contract, not a hint.

    With one boundary, which the geometry imposes rather than the code: a curled fingertip sits
    close to the thumb's own knuckle, so a gap wider than that distance cannot be held without the
    thumb pointing away from the finger it is open against. There the thumb stops at its knuckle
    and the pose stays unambiguously open, which is all a caller asking for 1.1 ever wanted.

    Scale 1.0 and aspect 1.0 make normalized distance and hand-scale units the same number.
    """
    resting = make_hand(pose, scale=1.0, aspect=1.0)

    for target, kwargs in ((INDEX_TIP, {"pinch_index": gap}), (MIDDLE_TIP, {"pinch_middle": gap})):
        hand = make_hand(pose, scale=1.0, aspect=1.0, **kwargs)
        measured = _distance(hand[4], hand[target])

        # Where the boundary sits is the implementation's business; that there is one, and what
        # happens on each side of it, is this test's.
        if gap <= _distance(resting[1], resting[target]) - _MIN_THUMB_REACH:
            assert measured == pytest.approx(gap, abs=1e-3)
        else:
            assert measured < gap
            assert measured > 0.5, "an unreachable gap must still read as an open hand"


@pytest.mark.parametrize("gap", GAPS)
@pytest.mark.parametrize("pose", POSES)
def test_the_thumb_stays_a_thumb_while_it_reaches(pose, gap: float) -> None:
    """No joint may sit further from the knuckle than the joint after it.

    This is what failed before: the tip was moved on its own to wherever the pinch distance
    required, leaving the knuckle behind and drawing a bone across the palm to a landmark no hand
    could reach.
    """
    hand = make_hand(pose, scale=1.0, aspect=1.0, pinch_index=gap)
    knuckle = hand[THUMB_CHAIN[0]]
    spans = [_distance(knuckle, hand[index]) for index in THUMB_CHAIN[1:]]

    assert spans == sorted(spans)
    assert spans[0] > 0.0


def test_a_resting_thumb_sits_where_the_hand_puts_it() -> None:
    """The demo script's open value must not deform the hand it is drawn on.

    `_OPEN` in `telemetry.py` is 0.9 — "not pinching", not "reaching somewhere specific". At that
    distance the thumb should look untouched, which means its reach from the knuckle should land
    within a few per cent of the length the pose itself would have given it.
    """
    resting = make_hand(OPEN_HAND, scale=1.0, aspect=1.0)
    reaching = make_hand(OPEN_HAND, scale=1.0, aspect=1.0, pinch_index=0.9)

    natural = _distance(resting[1], resting[4])
    assert _distance(reaching[1], reaching[4]) == pytest.approx(natural, rel=0.05)


def test_the_thumb_never_folds_back_through_its_own_knuckle() -> None:
    """A gap wider than the hand is asked for by the demo's fist step, which is never drawn.

    Left unguarded it produced a negative reach — a thumb pointing away from the finger it was
    supposedly holding open against.
    """
    hand = make_hand(FIST, scale=1.0, aspect=1.0, pinch_index=3.0)
    knuckle, tip = hand[1], hand[4]
    target = hand[INDEX_TIP]

    toward_target = (target[0] - knuckle[0], target[1] - knuckle[1])
    toward_tip = (tip[0] - knuckle[0], tip[1] - knuckle[1])

    assert toward_target[0] * toward_tip[0] + toward_target[1] * toward_tip[1] > 0.0
