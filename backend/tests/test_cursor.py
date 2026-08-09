"""Cursor Engine.

Nothing here touches the real cursor: every test drives a recording backend. That is a hard rule
in `memory-bank/systemPatterns.md`, and it is also the only way a test suite that synthesizes
mouse input can be run without hijacking the machine it runs on.

Two groups. Mapping is pure geometry, so it is tested exhaustively. The engine tests are almost
all safety cases, because the failure modes here are not wrong pixels — they are a stuck mouse
button or a pointer the user cannot take back.
"""

from __future__ import annotations

import pytest

from airhand.cursor import ActiveArea, CursorEngine, ScreenSize, active_area_for, to_screen
from airhand.gestures import GestureEvent, GestureEventType, GestureUpdate

# The dev machine: an ultrawide paired with a 4:3 camera, which is the case that exposes
# non-uniform gain most brutally.
ULTRAWIDE = ScreenSize(2560, 1080)
FRAME_4_3 = 4 / 3


class RecordingBackend:
    """Captures calls instead of performing them."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def move(self, x: int, y: int) -> None:
        self.calls.append(("move", x, y))

    def press_left(self) -> None:
        self.calls.append(("press_left",))

    def release_left(self) -> None:
        self.calls.append(("release_left",))

    def click_left(self) -> None:
        self.calls.append(("click_left",))

    def click_right(self) -> None:
        self.calls.append(("click_right",))

    def scroll(self, steps: int) -> None:
        self.calls.append(("scroll", steps))

    @property
    def actions(self) -> list[str]:
        """Everything except cursor movement, which happens on every frame."""
        return [call[0] for call in self.calls if call[0] != "move"]


# ------------------------------------------------------------------------ mapping


def test_active_area_matches_the_screen_aspect_not_the_camera() -> None:
    area = active_area_for(screen_aspect=ULTRAWIDE.aspect, frame_aspect=FRAME_4_3, coverage=0.7)

    # In pixels the region must be as wide-to-tall as the display.
    pixel_aspect = (area.width / area.height) * FRAME_4_3
    assert pixel_aspect == pytest.approx(ULTRAWIDE.aspect, rel=1e-6)
    assert area.width == pytest.approx(0.7)
    assert area.height == pytest.approx(0.7 * FRAME_4_3 / ULTRAWIDE.aspect, rel=1e-6)


def test_active_area_is_centred() -> None:
    area = active_area_for(screen_aspect=ULTRAWIDE.aspect, frame_aspect=FRAME_4_3)
    assert area.left + area.width / 2 == pytest.approx(0.5)
    assert area.top + area.height / 2 == pytest.approx(0.5)


def test_a_measured_centre_moves_the_area_without_resizing_it() -> None:
    """What the Calibration screen's `neutral` step produces: an off-centre comfortable position."""
    centred = active_area_for(screen_aspect=ULTRAWIDE.aspect, frame_aspect=FRAME_4_3, coverage=0.6)
    shifted = active_area_for(
        screen_aspect=ULTRAWIDE.aspect,
        frame_aspect=FRAME_4_3,
        coverage=0.6,
        center=(0.35, 0.6),
    )

    assert shifted.left + shifted.width / 2 == pytest.approx(0.35)
    assert shifted.top + shifted.height / 2 == pytest.approx(0.6)
    # Size untouched: sensitivity is `coverage`'s job, and moving the region must not change it.
    assert shifted.width == pytest.approx(centred.width)
    assert shifted.height == pytest.approx(centred.height)


def test_a_centre_near_the_edge_slides_the_area_back_instead_of_trimming_it() -> None:
    """Trimming would change the aspect, and with it the gain uniformity in the other tests."""
    area = active_area_for(
        screen_aspect=ULTRAWIDE.aspect, frame_aspect=FRAME_4_3, coverage=0.8, center=(0.05, 0.95)
    )

    assert area.left == pytest.approx(0.0)
    assert area.bottom == pytest.approx(1.0)
    assert area.width == pytest.approx(0.8)
    assert (area.width / area.height) * FRAME_4_3 == pytest.approx(ULTRAWIDE.aspect, rel=1e-6)


def test_the_default_centre_reproduces_the_old_behaviour_exactly() -> None:
    """Regression: `center` arrived after the mapping shipped, so its default must change nothing."""
    for coverage in (0.3, 0.7, 1.0):
        explicit = active_area_for(
            screen_aspect=ULTRAWIDE.aspect,
            frame_aspect=FRAME_4_3,
            coverage=coverage,
            center=(0.5, 0.5),
        )
        implicit = active_area_for(
            screen_aspect=ULTRAWIDE.aspect, frame_aspect=FRAME_4_3, coverage=coverage
        )
        assert explicit == implicit
        assert implicit.left == pytest.approx((1.0 - implicit.width) / 2.0)


def test_a_hand_resting_at_the_measured_centre_lands_in_the_middle_of_the_screen() -> None:
    """The bug a saved profile exposed on 2026-08-08.

    `to_screen` used to mirror `x` *before* subtracting `area.left`, which put the area in mirrored
    coordinates while landmarks, the pointer anchor and the centre Calibration measures are all raw
    frame coordinates. An area centred on a measured resting position of 0.61 therefore sat at
    0.39 — reflected to the other side of the frame, costing the user half their reach.

    Invisible while the area was centred, because both orders agree exactly there.
    """
    area = active_area_for(
        screen_aspect=ULTRAWIDE.aspect, frame_aspect=FRAME_4_3, coverage=0.5, center=(0.65, 0.5)
    )

    x, _ = to_screen(0.65, 0.5, area=area, screen_width=2560, screen_height=1080)
    assert x == pytest.approx((2560 - 1) / 2, abs=1)

    # And the sign convention still holds around that centre: the user's hand moving right makes
    # `x` in the frame *decrease*, and the cursor must go right.
    right, _ = to_screen(0.55, 0.5, area=area, screen_width=2560, screen_height=1080)
    left, _ = to_screen(0.75, 0.5, area=area, screen_width=2560, screen_height=1080)
    assert right > x > left


def test_active_area_shrinks_rather_than_distorting_when_it_will_not_fit() -> None:
    """A tall screen with a wide frame can demand more height than exists."""
    area = active_area_for(screen_aspect=0.5, frame_aspect=FRAME_4_3, coverage=1.0)
    assert area.height <= 1.0
    assert (area.width / area.height) * FRAME_4_3 == pytest.approx(0.5, rel=1e-6)


def test_centre_of_active_area_maps_to_centre_of_screen() -> None:
    area = active_area_for(screen_aspect=ULTRAWIDE.aspect, frame_aspect=FRAME_4_3)
    x, y = to_screen(0.5, 0.5, area=area, screen_width=2560, screen_height=1080)
    assert x == pytest.approx(1279, abs=1)
    assert y == pytest.approx(539, abs=1)


def test_hand_moving_right_moves_the_cursor_right() -> None:
    """The camera faces the user, so their right is the frame's left and x decreases.

    Without mirroring the cursor travels the wrong way, which reads as a broken app rather than a
    sign convention.
    """
    area = ActiveArea(left=0.2, top=0.2, width=0.6, height=0.6)
    left_of_frame, _ = to_screen(0.3, 0.5, area=area, screen_width=1000, screen_height=1000)
    right_of_frame, _ = to_screen(0.7, 0.5, area=area, screen_width=1000, screen_height=1000)
    assert left_of_frame > right_of_frame


def test_gain_is_uniform_in_both_axes() -> None:
    """Equal physical hand movement must travel equally far on screen.

    This is the bug the aspect-matched active area exists to prevent: with a 4:3 region stretched
    onto 21:9, horizontal movement would be amplified about 1.8x more than vertical.
    """
    area = active_area_for(screen_aspect=ULTRAWIDE.aspect, frame_aspect=FRAME_4_3)

    # One isotropic unit is a fraction of frame *height*; horizontally that is /frame_aspect.
    travel = 0.05
    base_x, base_y = to_screen(
        0.5, 0.5, area=area, screen_width=ULTRAWIDE.width, screen_height=ULTRAWIDE.height
    )
    moved_x, _ = to_screen(
        0.5 + travel / FRAME_4_3,
        0.5,
        area=area,
        screen_width=ULTRAWIDE.width,
        screen_height=ULTRAWIDE.height,
    )
    _, moved_y = to_screen(
        0.5,
        0.5 + travel,
        area=area,
        screen_width=ULTRAWIDE.width,
        screen_height=ULTRAWIDE.height,
    )

    assert abs(moved_x - base_x) == pytest.approx(abs(moved_y - base_y), rel=0.02)


def test_positions_outside_the_active_area_clamp_to_the_screen_edge() -> None:
    """Corners must be reachable without pushing the hand out of the camera's view."""
    area = ActiveArea(left=0.25, top=0.25, width=0.5, height=0.5)
    x, y = to_screen(0.0, 0.0, area=area, screen_width=1920, screen_height=1080, mirror=False)
    assert (x, y) == (0, 0)

    x, y = to_screen(1.0, 1.0, area=area, screen_width=1920, screen_height=1080, mirror=False)
    assert (x, y) == (1919, 1079)


def test_smaller_active_area_means_higher_sensitivity() -> None:
    wide = active_area_for(screen_aspect=2.0, frame_aspect=FRAME_4_3, coverage=0.9)
    narrow = active_area_for(screen_aspect=2.0, frame_aspect=FRAME_4_3, coverage=0.4)

    def travel(area: ActiveArea) -> int:
        start, _ = to_screen(0.48, 0.5, area=area, screen_width=1920, screen_height=1080)
        end, _ = to_screen(0.52, 0.5, area=area, screen_width=1920, screen_height=1080)
        return abs(end - start)

    assert travel(narrow) > travel(wide)


def test_coverage_must_be_a_sane_fraction() -> None:
    with pytest.raises(ValueError):
        active_area_for(screen_aspect=2.0, frame_aspect=FRAME_4_3, coverage=0.0)


# ------------------------------------------------------------------------- engine


def _engine(backend: RecordingBackend) -> CursorEngine:
    return CursorEngine(backend=backend, screen=ULTRAWIDE, coverage=0.7)


def _update(*events: GestureEventType, scroll_steps: int = 0) -> GestureUpdate:
    return GestureUpdate(
        "move",
        tuple(GestureEvent(event, scroll_steps) for event in events),
    )


def test_disabled_by_default_and_emits_nothing() -> None:
    """Actuation must be opted into every session, never inherited."""
    backend = RecordingBackend()
    engine = _engine(backend)

    assert engine.enabled is False
    engine.handle(_update(GestureEventType.LEFT_CLICK), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3)
    assert backend.calls == []


def test_enabling_allows_movement_and_clicks() -> None:
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    engine.handle(_update(GestureEventType.LEFT_CLICK), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3)
    engine.handle(_update(GestureEventType.RIGHT_CLICK), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3)

    assert backend.actions == ["click_left", "click_right"]
    assert any(call[0] == "move" for call in backend.calls)


def test_losing_the_hand_mid_drag_releases_the_button() -> None:
    """The worst failure this module can produce: a machine left with the left button held down."""
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    engine.handle(_update(GestureEventType.DRAG_START), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3)
    assert backend.actions == ["press_left"]

    engine.handle(_update(), anchor=None, frame_aspect=FRAME_4_3)
    assert backend.actions == ["press_left", "release_left"]


def test_disabling_mid_drag_releases_the_button() -> None:
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    engine.handle(_update(GestureEventType.DRAG_START), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3)
    engine.set_enabled(False)

    assert backend.actions == ["press_left", "release_left"]


def test_release_all_is_idempotent() -> None:
    """It runs from shutdown paths and exception handlers; a second call must be harmless."""
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    engine.handle(_update(GestureEventType.DRAG_START), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3)
    engine.release_all()
    engine.release_all()

    assert backend.actions.count("release_left") == 1


def test_drag_start_twice_presses_once() -> None:
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    engine.handle(_update(GestureEventType.DRAG_START), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3)
    engine.handle(_update(GestureEventType.DRAG_START), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3)

    assert backend.actions == ["press_left"]


def test_movement_continues_during_a_drag() -> None:
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    engine.handle(_update(GestureEventType.DRAG_START), anchor=(0.45, 0.5), frame_aspect=FRAME_4_3)
    engine.handle(_update(), anchor=(0.55, 0.5), frame_aspect=FRAME_4_3)

    moves = [call for call in backend.calls if call[0] == "move"]
    assert len(moves) == 2
    assert moves[0][1] != moves[1][1], "a drag that cannot move cannot drag anything"


def test_scroll_steps_are_forwarded() -> None:
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    engine.handle(
        _update(GestureEventType.SCROLL, scroll_steps=3), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3
    )
    assert ("scroll", 3) in backend.calls


def test_zero_scroll_steps_are_not_forwarded() -> None:
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    engine.handle(
        _update(GestureEventType.SCROLL, scroll_steps=0), anchor=(0.5, 0.5), frame_aspect=FRAME_4_3
    )
    assert "scroll" not in backend.actions


def test_cursor_never_leaves_the_screen() -> None:
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    for x, y in [(-5.0, -5.0), (5.0, 5.0), (0.0, 1.0), (1.0, 0.0)]:
        engine.handle(_update(), anchor=(x, y), frame_aspect=FRAME_4_3)

    for call in backend.calls:
        if call[0] == "move":
            assert 0 <= call[1] < ULTRAWIDE.width
            assert 0 <= call[2] < ULTRAWIDE.height


def test_set_mapping_changes_sensitivity_and_invalidates_the_cached_area() -> None:
    """The cached active area is keyed on frame aspect alone, so nothing else would drop it."""
    backend = RecordingBackend()
    engine = _engine(backend)
    engine.set_enabled(True)

    def travel() -> int:
        backend.calls.clear()
        engine.handle(_update(), anchor=(0.48, 0.5), frame_aspect=FRAME_4_3)
        engine.handle(_update(), anchor=(0.52, 0.5), frame_aspect=FRAME_4_3)
        moves = [call for call in backend.calls if call[0] == "move"]
        return abs(moves[1][1] - moves[0][1])

    wide = travel()
    engine.set_mapping(coverage=0.4)
    assert travel() > wide, "a smaller active area must move the cursor further per hand movement"


def test_set_mapping_moves_the_centre_and_drops_the_cache() -> None:
    engine = _engine(RecordingBackend())
    assert engine.active_area(FRAME_4_3).left == pytest.approx((1.0 - 0.7) / 2.0)

    engine.set_mapping(coverage=0.7, center_x=0.4, center_y=0.5)
    area = engine.active_area(FRAME_4_3)
    assert area.left + area.width / 2 == pytest.approx(0.4)


def test_set_mapping_refuses_a_value_that_would_break_the_mapping() -> None:
    engine = _engine(RecordingBackend())
    engine.set_mapping(coverage=0.5, center_x=0.45, center_y=0.55)

    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            engine.set_mapping(coverage=bad)
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError):
            engine.set_mapping(coverage=0.5, center_x=bad)

    # Refused, so the last good values must still be the ones in force — both of them, since the
    # check happens before either is assigned.
    area = engine.active_area(FRAME_4_3)
    assert area.width == pytest.approx(0.5)
    assert area.left + area.width / 2 == pytest.approx(0.45)


def test_state_reports_screen_and_enablement() -> None:
    engine = _engine(RecordingBackend())
    state = engine.state()
    assert state.available is True
    assert state.enabled is False
    assert (state.screen_width, state.screen_height) == (2560, 1080)

    assert engine.set_enabled(True).enabled is True
