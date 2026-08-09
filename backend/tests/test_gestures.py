"""Gesture Engine.

The headline cases are the camera-independence ones: the same pose must classify identically
regardless of hand size, frame aspect ratio or frame rate. Those three are the whole reason
`features.py` exists, and asserting them is the difference between a design claim and a fact.
"""

from __future__ import annotations

import pytest

from airhand.gestures import GestureConfig, GestureEngine, extract
from airhand.gestures.features import FINGER_NAMES

from tests.fixtures.hands import (
    FIST,
    OPEN_HAND,
    POINTING,
    SCROLL_POSE,
    make_hand,
)

ASPECTS = [4 / 3, 16 / 9, 1.0]
SCALES = [0.12, 0.25, 0.45]


def _engine(**overrides) -> GestureEngine:
    return GestureEngine(config=GestureConfig(**overrides))


def _run(engine: GestureEngine, landmarks, *, aspect: float, now: float) -> str:
    return engine.update(landmarks, aspect=aspect, now=now).gesture


def _events(engine: GestureEngine, landmarks, *, aspect: float, now: float) -> list[str]:
    update = engine.update(landmarks, aspect=aspect, now=now)
    return [event.type.value for event in update.events]


# --------------------------------------------------------------------- features


def test_hand_scale_is_independent_of_apparent_size() -> None:
    """A hand twice as close is twice as big in pixels; ratios against hand scale must not move."""
    ratios = []
    for scale in SCALES:
        features = extract(
            make_hand(OPEN_HAND, scale=scale, pinch_index=0.9),
            aspect=1.0,
            extended_angle_degrees=150.0,
        )
        assert features is not None
        ratios.append(features.pinch_index)

    assert max(ratios) - min(ratios) < 0.01


def test_features_are_independent_of_frame_aspect_ratio() -> None:
    """Raw normalized coordinates are anisotropic on a 16:9 sensor; corrected ones are not."""
    ratios = []
    for aspect in ASPECTS:
        features = extract(
            make_hand(OPEN_HAND, aspect=aspect, pinch_index=0.9),
            aspect=aspect,
            extended_angle_degrees=150.0,
        )
        assert features is not None
        ratios.append(features.pinch_index)

    assert max(ratios) - min(ratios) < 0.01


def test_extended_fingers_are_detected() -> None:
    features = extract(make_hand(OPEN_HAND), aspect=1.0, extended_angle_degrees=150.0)
    assert features is not None
    assert all(features.extended[name] for name in FINGER_NAMES)


def test_curled_fingers_are_detected() -> None:
    features = extract(make_hand(FIST), aspect=1.0, extended_angle_degrees=150.0)
    assert features is not None
    assert not any(features.extended[name] for name in FINGER_NAMES)


def test_degenerate_hand_yields_no_features() -> None:
    """All landmarks collapsed to a point gives a zero hand scale — dividing by it would be noise."""
    assert extract([[0.5, 0.5, 0.0]] * 21, aspect=1.0, extended_angle_degrees=150.0) is None


def test_short_landmark_list_yields_no_features() -> None:
    assert extract([[0.5, 0.5, 0.0]] * 5, aspect=1.0, extended_angle_degrees=150.0) is None


# ------------------------------------------------------------------ classification


def test_pointing_hand_is_move() -> None:
    engine = _engine()
    assert _run(engine, make_hand(POINTING, pinch_index=0.9), aspect=1.0, now=0.0) == "move"


def test_scroll_pose_is_scroll() -> None:
    engine = _engine()
    assert _run(engine, make_hand(SCROLL_POSE, pinch_index=0.9), aspect=1.0, now=0.0) == "scroll"


def test_fist_is_none() -> None:
    engine = _engine()
    assert _run(engine, make_hand(FIST, pinch_index=0.9), aspect=1.0, now=0.0) == "none"


def test_no_hand_is_none() -> None:
    engine = _engine()
    update = engine.update(None, aspect=1.0, now=0.0)
    assert update.gesture == "none"
    assert update.debug is None


def test_quick_pinch_release_is_a_left_click() -> None:
    engine = _engine()
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _run(engine, open_hand, aspect=1.0, now=0.0)
    # Held below the drag threshold, so the release resolves it as a click.
    assert _run(engine, pinched, aspect=1.0, now=0.10) == "none"
    assert _run(engine, open_hand, aspect=1.0, now=0.25) == "left_click"


def test_quick_middle_pinch_release_is_a_right_click() -> None:
    engine = _engine()
    open_hand = make_hand(POINTING, pinch_middle=0.9)
    pinched = make_hand(POINTING, pinch_middle=0.15)

    _run(engine, open_hand, aspect=1.0, now=0.0)
    _run(engine, pinched, aspect=1.0, now=0.10)
    assert _run(engine, open_hand, aspect=1.0, now=0.25) == "right_click"


# ------------------------------------------------- one gesture, one click
#
# Every test above poses the hand as POINTING, which holds the index finger rigidly out of the
# way. A hand does not do that. When the index is curled — which is what it does while the thumb
# reaches across to the middle finger — the two fingertips sit beside each other, and the thumb
# cannot approach one without approaching the other.
#
# Measured on this same fixture (2026-08-09): a right click with the index curled reads
# `pinch_index` 0.264 against `pinch_middle` 0.150. Both are under a threshold of 0.4695, which is
# what a real calibration produced. The fixture could always build this pose; no test asked for it.


def _right_click_with_index_curled(gap: float):
    """Thumb reaching the middle fingertip with the rest of the hand closed."""
    return make_hand(FIST, pinch_middle=gap)


@pytest.mark.parametrize("close", [0.4695, 0.50])
def test_a_right_click_with_a_curled_index_fires_one_click_not_two(close: float) -> None:
    """Both pinch pairs close, so both used to resolve — a left *and* a right click.

    Parametrized over the threshold a real calibration produced and the shipped default, because
    both reproduce it. The rule that fixes it is ordinal, so neither value should matter.
    """
    engine = _engine(pinch_close=close, pinch_open=close + 0.20)

    _events(engine, _right_click_with_index_curled(1.10), aspect=1.0, now=0.00)
    _events(engine, _right_click_with_index_curled(0.15), aspect=1.0, now=0.05)
    _events(engine, _right_click_with_index_curled(0.15), aspect=1.0, now=0.10)
    fired = _events(engine, _right_click_with_index_curled(1.10), aspect=1.0, now=0.20)

    assert fired == ["right_click"]


def test_the_pinch_that_went_deepest_owns_the_gesture() -> None:
    """Which finger the thumb actually reached, not which threshold it crossed first.

    Depth over ordering on purpose: the two distances cross `pinch_close` within a frame or two of
    each other and in an order that depends on the hand, while the finger the thumb is touching is
    unambiguous for the whole closure.
    """
    engine = _engine(pinch_close=0.4695, pinch_open=0.6695)

    _events(engine, _right_click_with_index_curled(1.10), aspect=1.0, now=0.00)
    _events(engine, _right_click_with_index_curled(0.15), aspect=1.0, now=0.05)

    debug = engine.update(_right_click_with_index_curled(0.15), aspect=1.0, now=0.10).debug
    assert debug is not None
    # Both really are closed — the test would pass vacuously if only one were.
    assert debug.pinch_index < 0.4695
    assert debug.pinch_middle < debug.pinch_index


def test_a_staggered_release_still_resolves_to_the_deeper_pinch() -> None:
    """The index sits further from the thumb, so it crosses `pinch_open` *first*.

    A rule that only arbitrated releases landing in the same frame would miss this entirely, and
    this is the ordering a real hand produces.
    """
    engine = _engine(pinch_close=0.4695, pinch_open=0.6695)

    _events(engine, _right_click_with_index_curled(1.10), aspect=1.0, now=0.00)
    _events(engine, _right_click_with_index_curled(0.15), aspect=1.0, now=0.05)
    # Partly open: far enough for the index pair to release, not for the middle pair.
    midway = _events(engine, make_hand(FIST, pinch_middle=0.55), aspect=1.0, now=0.10)
    assert midway == []

    assert _events(engine, _right_click_with_index_curled(1.10), aspect=1.0, now=0.15) == [
        "right_click"
    ]


def test_a_held_right_click_does_not_become_a_drag() -> None:
    """Drag belongs to the index pinch, and the index pair is only incidentally closed here.

    The quieter half of the same defect: without arbitration a long right click starts dragging,
    which holds a real mouse button down.
    """
    engine = _engine(pinch_close=0.4695, pinch_open=0.6695, hold_to_drag_seconds=0.4)

    _events(engine, _right_click_with_index_curled(1.10), aspect=1.0, now=0.00)
    _events(engine, _right_click_with_index_curled(0.15), aspect=1.0, now=0.05)
    fired = _events(engine, _right_click_with_index_curled(0.15), aspect=1.0, now=0.60)

    assert fired == []
    assert engine.update(
        _right_click_with_index_curled(0.15), aspect=1.0, now=0.70
    ).gesture != "drag"


def test_arbitration_does_not_touch_a_plain_left_click() -> None:
    """The index pinch is unambiguous — the middle finger is nowhere near the thumb."""
    engine = _engine(pinch_close=0.4695, pinch_open=0.6695)
    open_hand = make_hand(POINTING, pinch_index=0.9)

    _events(engine, open_hand, aspect=1.0, now=0.00)
    _events(engine, make_hand(POINTING, pinch_index=0.15), aspect=1.0, now=0.05)
    assert _events(engine, open_hand, aspect=1.0, now=0.20) == ["left_click"]


def test_held_pinch_becomes_a_drag_not_a_click() -> None:
    engine = _engine()
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _run(engine, open_hand, aspect=1.0, now=0.0)
    _run(engine, pinched, aspect=1.0, now=0.05)
    assert _run(engine, pinched, aspect=1.0, now=0.60) == "drag"

    # Releasing a drag must not also emit a click.
    released = _run(engine, open_hand, aspect=1.0, now=0.90)
    assert released != "left_click"


def test_click_is_latched_long_enough_for_a_10hz_client_to_see_it() -> None:
    """Clicks are events on a state field; a single-frame flash would be invisible at 10 Hz."""
    engine = _engine(click_latch_seconds=0.2)
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _run(engine, open_hand, aspect=1.0, now=0.0)
    _run(engine, pinched, aspect=1.0, now=0.05)
    assert _run(engine, open_hand, aspect=1.0, now=0.20) == "left_click"
    assert _run(engine, open_hand, aspect=1.0, now=0.35) == "left_click"
    # ...but it must expire, or the UI would show a click forever.
    assert _run(engine, open_hand, aspect=1.0, now=0.45) == "move"


# ------------------------------------------------------------------- robustness


def test_hysteresis_prevents_a_burst_of_clicks_at_the_threshold() -> None:
    """A hand resting near the close threshold is the classic phantom-click generator."""
    config = GestureConfig()
    engine = GestureEngine(config=config)
    midpoint = (config.pinch_close + config.pinch_open) / 2

    clicks = 0
    now = 0.0
    _run(engine, make_hand(POINTING, pinch_index=0.9), aspect=1.0, now=now)

    # Dither around the band without ever fully opening.
    for step in range(40):
        gap = config.pinch_close - 0.02 if step % 2 == 0 else midpoint
        now += 1 / 30
        if _run(engine, make_hand(POINTING, pinch_index=gap), aspect=1.0, now=now) == "left_click":
            clicks += 1

    assert clicks == 0, "dithering inside the hysteresis band must not produce clicks"


@pytest.mark.parametrize("aspect", ASPECTS)
def test_classification_is_identical_across_aspect_ratios(aspect: float) -> None:
    engine = _engine()
    assert _run(engine, make_hand(SCROLL_POSE, aspect=aspect, pinch_index=0.9),
                aspect=aspect, now=0.0) == "scroll"


@pytest.mark.parametrize("scale", SCALES)
def test_classification_is_identical_across_hand_sizes(scale: float) -> None:
    """Same gesture, hand nearer or further from the lens."""
    engine = _engine()
    open_hand = make_hand(POINTING, scale=scale, pinch_index=0.9)
    pinched = make_hand(POINTING, scale=scale, pinch_index=0.15)

    _run(engine, open_hand, aspect=1.0, now=0.0)
    _run(engine, pinched, aspect=1.0, now=0.10)
    assert _run(engine, open_hand, aspect=1.0, now=0.25) == "left_click"


@pytest.mark.parametrize("fps", [30, 60])
def test_click_and_drag_split_at_the_same_wall_clock_time_on_any_frame_rate(fps: int) -> None:
    """A frame-counted hold would trigger twice as fast at 60 fps — the same gesture would mean
    different things on different webcams."""
    engine = _engine(hold_to_drag_seconds=0.4)
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    now = 0.0
    _run(engine, open_hand, aspect=1.0, now=now)

    gestures = []
    for _ in range(int(0.3 * fps)):
        now += 1 / fps
        gestures.append(_run(engine, pinched, aspect=1.0, now=now))
    assert "drag" not in gestures, "0.3 s is under the 0.4 s threshold at every frame rate"

    for _ in range(int(0.2 * fps)):
        now += 1 / fps
        gestures.append(_run(engine, pinched, aspect=1.0, now=now))
    assert gestures[-1] == "drag", "0.5 s is over the threshold at every frame rate"


def test_config_rejects_a_missing_hysteresis_gap() -> None:
    with pytest.raises(ValueError, match="hysteresis"):
        GestureConfig(pinch_close=0.5, pinch_open=0.5)


# ------------------------------------------------------------------------ debug


def test_debug_reports_features_and_thresholds() -> None:
    engine = _engine()
    debug = engine.update(make_hand(POINTING, pinch_index=0.9), aspect=1.0, now=0.0).debug
    assert debug is not None

    message = debug.to_message()
    assert message["state"] == "move"
    assert message["pinchIndex"] == pytest.approx(0.9, abs=0.05)
    assert set(message["extended"]) == set(FINGER_NAMES)
    # Thresholds ride along so a client can draw them without hardcoding a second copy. Read from
    # the config rather than repeated as literals — this test exists to prove they are *reported*,
    # not to pin the tuning, and pinning it here made the file fail on the first real re-tune.
    config = GestureConfig()
    assert message["thresholds"] == {
        "pinchClose": config.pinch_close,
        "pinchOpen": config.pinch_open,
    }


# -------------------------------------------------------------- synthetic source


def test_synthetic_source_script_produces_every_gesture_through_the_real_engine() -> None:
    """The synthetic source must not assert labels — it poses a hand and lets the engine decide.

    This is what keeps the no-webcam demo honest: an earlier version fabricated the gesture string,
    so the overlay could show an open hand while the readout claimed a pinch. Driving the real
    engine makes disagreement impossible and exercises the classification path for free.
    """
    from airhand.telemetry import SyntheticSource

    source = SyntheticSource(seed=3)
    source.start()

    seen: set[str] = set()
    elapsed = 0.0
    # One full script cycle, sampled at a realistic frame rate. `_sample` is the deterministic
    # seam; `latest()` reads the wall clock and could not be swept.
    while elapsed < source._cycle_length:  # noqa: SLF001 - deliberate, see above
        seen.add(source._sample(elapsed).gesture)  # noqa: SLF001
        elapsed += 1 / 60

    assert {"none", "move", "left_click", "right_click", "drag", "scroll"} <= seen, (
        f"script did not exercise every gesture; got {sorted(seen)}"
    )


def test_pointer_hold_is_raised_only_while_a_pinch_is_undecided() -> None:
    """The signal the pointer stage freezes on, so a click lands where the user aimed.

    Phrased as a fact about the gesture — "a click may be under way" — not about the cursor. It
    must be false during a drag: a drag that cannot move cannot drag anything.
    """
    engine = _engine()
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    assert engine.update(open_hand, aspect=1.0, now=0.0).pointer_hold is False
    assert engine.update(pinched, aspect=1.0, now=0.05).pointer_hold is True
    # Past the drag threshold the pinch is resolved, so the pointer follows the hand again.
    assert engine.update(pinched, aspect=1.0, now=0.60).pointer_hold is False
    assert engine.update(open_hand, aspect=1.0, now=0.90).pointer_hold is False


def test_pointer_hold_is_false_without_a_hand() -> None:
    engine = _engine()
    assert engine.update(None, aspect=1.0, now=0.0).pointer_hold is False


def test_pointer_hold_covers_the_right_click_pinch_too() -> None:
    engine = _engine()
    engine.update(make_hand(POINTING, pinch_middle=0.9), aspect=1.0, now=0.0)
    assert engine.update(make_hand(POINTING, pinch_middle=0.15), aspect=1.0, now=0.05).pointer_hold


def test_pointer_hold_is_false_while_scrolling() -> None:
    engine = _engine()
    scrolling = make_hand(SCROLL_POSE, pinch_index=0.9)
    assert engine.update(scrolling, aspect=1.0, now=0.0).pointer_hold is False


def test_debug_exposes_the_undecided_pinch_state() -> None:
    """`gesture` stays honest while a pinch is unresolved; the pending state lives in debug."""
    engine = _engine()
    _run(engine, make_hand(POINTING, pinch_index=0.9), aspect=1.0, now=0.0)
    update = engine.update(make_hand(POINTING, pinch_index=0.15), aspect=1.0, now=0.05)

    assert update.gesture == "none"
    assert update.debug is not None
    assert update.debug.state == "pinch_index_pending"


# ------------------------------------------------------------------------ events


def test_click_emits_exactly_one_event_despite_the_display_latch() -> None:
    """The latch keeps `gesture` reading `left_click` for ~200 ms so a 10 Hz UI can see it.

    Actuation must not follow that: acting on the latched field would fire one click per frame for
    the whole window. Events fire once, on the release.
    """
    engine = _engine()
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _events(engine, open_hand, aspect=1.0, now=0.0)
    _events(engine, pinched, aspect=1.0, now=0.10)

    emitted = _events(engine, open_hand, aspect=1.0, now=0.25)
    assert emitted == ["left_click"]

    # The gesture field is still latched here, but no further events may appear.
    assert _run(engine, open_hand, aspect=1.0, now=0.30) == "left_click"
    assert _events(engine, open_hand, aspect=1.0, now=0.35) == []


def test_drag_emits_start_and_end_but_no_click() -> None:
    engine = _engine()
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _events(engine, open_hand, aspect=1.0, now=0.0)
    _events(engine, pinched, aspect=1.0, now=0.05)
    assert _events(engine, pinched, aspect=1.0, now=0.60) == ["drag_start"]

    emitted = _events(engine, open_hand, aspect=1.0, now=0.90)
    assert emitted == ["drag_end"]
    assert "left_click" not in emitted, "a completed drag is not also a click"


def test_losing_the_hand_mid_drag_emits_drag_end() -> None:
    """Without this the Cursor Engine never hears the release and leaves the button held."""
    engine = _engine()
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _events(engine, open_hand, aspect=1.0, now=0.0)
    _events(engine, pinched, aspect=1.0, now=0.05)
    _events(engine, pinched, aspect=1.0, now=0.60)  # drag_start

    assert _events(engine, None, aspect=1.0, now=0.70) == ["drag_end"]
    # ...and only once.
    assert _events(engine, None, aspect=1.0, now=0.80) == []


# ---------------------------------------------------------------------- dropouts

# A pinch is the moment MediaPipe is most likely to lose the hand: the thumb goes behind the index
# finger. A click is defined by the *release*, so losing the "it was closed" bookkeeping for even
# one frame used to swallow the click entirely — silently, and at random.


def test_a_single_lost_frame_does_not_swallow_the_click() -> None:
    """The bug this whole grace window exists for."""
    engine = _engine()
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _events(engine, open_hand, aspect=1.0, now=0.0)
    _events(engine, pinched, aspect=1.0, now=0.05)
    # MediaPipe blinks while the fingers overlap.
    assert _events(engine, None, aspect=1.0, now=0.08) == []
    assert _events(engine, open_hand, aspect=1.0, now=0.12) == ["left_click"]


def test_a_gap_longer_than_the_grace_starts_over() -> None:
    """Beyond the window the old pinch is not evidence about the new hand."""
    engine = _engine(dropout_grace_seconds=0.15)
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _events(engine, open_hand, aspect=1.0, now=0.0)
    _events(engine, pinched, aspect=1.0, now=0.05)
    for step in range(1, 8):  # 0.35 s of nothing
        _events(engine, None, aspect=1.0, now=0.05 + step * 0.05)

    assert _events(engine, open_hand, aspect=1.0, now=0.60) == []


def test_invisible_time_does_not_count_toward_the_drag_threshold() -> None:
    """Otherwise surviving a dropout would trade a missing click for an unwanted drag.

    The pinch is held for 0.2 s of *visible* time, well under the 0.4 s threshold, but wall-clock
    covers 0.35 s because the hand vanished in the middle of it.
    """
    engine = _engine(hold_to_drag_seconds=0.4, dropout_grace_seconds=0.2)
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _events(engine, open_hand, aspect=1.0, now=0.0)
    _events(engine, pinched, aspect=1.0, now=0.05)
    _events(engine, None, aspect=1.0, now=0.10)  # gap starts
    _events(engine, pinched, aspect=1.0, now=0.25)  # 0.15 s invisible
    assert _events(engine, open_hand, aspect=1.0, now=0.40) == ["left_click"]


def test_losing_the_hand_mid_drag_still_releases_immediately() -> None:
    """The safety rule, and the reason the grace covers pinch state only.

    A held button on a hand nobody can see is the failure the whole cursor safety model exists to
    prevent. "It will probably come back" is not a reason to keep the mouse pressed.
    """
    engine = _engine(dropout_grace_seconds=0.5)
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _events(engine, open_hand, aspect=1.0, now=0.0)
    _events(engine, pinched, aspect=1.0, now=0.05)
    assert _events(engine, pinched, aspect=1.0, now=0.60) == ["drag_start"]

    # The very next frame, not after the grace expires.
    assert _events(engine, None, aspect=1.0, now=0.63) == ["drag_end"]
    # ...and exactly once, including after the window closes.
    assert _events(engine, None, aspect=1.0, now=0.70) == []
    assert _events(engine, None, aspect=1.0, now=1.50) == []


def test_a_dropout_resets_the_scroll_origin_even_inside_the_grace() -> None:
    """A stale origin would read as a large jump and emit a burst of wheel steps."""
    engine = _engine(scroll_step=0.25, dropout_grace_seconds=0.3)

    def scrolling(offset: float):
        return make_hand(SCROLL_POSE, center=(0.5, 0.6 + offset), pinch_index=0.9)

    engine.update(scrolling(0.0), aspect=1.0, now=0.0)
    _events(engine, None, aspect=1.0, now=0.05)
    assert _events(engine, scrolling(0.30), aspect=1.0, now=0.10) == []


@pytest.mark.parametrize("fps", [30, 60])
def test_the_grace_window_is_the_same_wall_clock_time_at_any_frame_rate(fps: int) -> None:
    """A frame-counted window would forgive twice as long a gap on a 60 fps camera."""
    engine = _engine(dropout_grace_seconds=0.15)
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    now = 0.0
    _events(engine, open_hand, aspect=1.0, now=now)
    now += 1 / fps
    _events(engine, pinched, aspect=1.0, now=now)

    for _ in range(int(0.10 * fps)):  # inside the window at either rate
        now += 1 / fps
        _events(engine, None, aspect=1.0, now=now)

    now += 1 / fps
    assert _events(engine, open_hand, aspect=1.0, now=now) == ["left_click"]


def test_grace_can_be_switched_off() -> None:
    """Zero restores the old behaviour exactly — which is what the bench compares against."""
    engine = _engine(dropout_grace_seconds=0.0)
    open_hand = make_hand(POINTING, pinch_index=0.9)
    pinched = make_hand(POINTING, pinch_index=0.15)

    _events(engine, open_hand, aspect=1.0, now=0.0)
    _events(engine, pinched, aspect=1.0, now=0.05)
    _events(engine, None, aspect=1.0, now=0.08)
    assert _events(engine, open_hand, aspect=1.0, now=0.12) == []


def test_a_negative_grace_is_refused() -> None:
    with pytest.raises(ValueError, match="dropout_grace_seconds"):
        GestureConfig(dropout_grace_seconds=-0.1)


def test_scroll_emits_steps_proportional_to_hand_travel() -> None:
    engine = _engine(scroll_step=0.25)
    aspect = 1.0

    def scrolling(offset: float):
        return make_hand(SCROLL_POSE, center=(0.5, 0.6 + offset), pinch_index=0.9)

    # First frame in the pose only establishes the origin.
    assert _events(engine, scrolling(0.0), aspect=aspect, now=0.0) == []

    update = engine.update(scrolling(0.20), aspect=aspect, now=0.1)
    steps = [event.scroll_steps for event in update.events if event.type.value == "scroll"]
    assert steps, "moving the hand while in the scroll pose must produce scroll steps"
    # Hand moved down the frame, so the wheel goes negative (content scrolls down).
    assert steps[0] < 0


def test_scroll_direction_is_inverted_between_axes() -> None:
    """Screen y grows downward, so moving the hand up must be a positive wheel step."""
    engine = _engine(scroll_step=0.25)

    def scrolling(offset: float):
        return make_hand(SCROLL_POSE, center=(0.5, 0.6 + offset), pinch_index=0.9)

    engine.update(scrolling(0.0), aspect=1.0, now=0.0)
    update = engine.update(scrolling(-0.20), aspect=1.0, now=0.1)
    steps = [event.scroll_steps for event in update.events if event.type.value == "scroll"]
    assert steps and steps[0] > 0


def test_leaving_the_scroll_pose_resets_the_origin() -> None:
    """Otherwise re-entering the pose elsewhere in the frame would emit a jump of stale travel."""
    engine = _engine(scroll_step=0.25)

    def scrolling(offset: float):
        return make_hand(SCROLL_POSE, center=(0.5, 0.6 + offset), pinch_index=0.9)

    engine.update(scrolling(0.0), aspect=1.0, now=0.0)
    _events(engine, make_hand(POINTING, pinch_index=0.9), aspect=1.0, now=0.1)
    assert _events(engine, scrolling(0.30), aspect=1.0, now=0.2) == []
