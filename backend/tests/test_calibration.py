"""Calibration measurement.

Everything here is fed synthetic observations, so a real camera and a real hand are never
involved. That is deliberate: the derivations are the part that has to be right, and they are
pure arithmetic over a series of numbers.

The recurring theme is **robustness to one bad frame**. A dropout, a hand that drifted at the end
of a hold, a single spike from an occluded thumb — none of them may be allowed to define the
result. That lesson was paid for twice already (the rest-jitter metric, then the lost clicks), so
it is pinned here as tests rather than left to a comment.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from airhand.calibration import (
    MIN_PINCH_ATTEMPTS,
    PINCH_PHASE_SECONDS,
    STEP_SECONDS,
    CalibrationSession,
    Observation,
)
from airhand.settings import DEFAULTS, merge

FPS = 30.0
NO_HAND = Observation(anchor=None, pinch_index=None, detected=False)


def _hand(anchor: tuple[float, float] = (0.5, 0.5), pinch: float = 0.95) -> Observation:
    return Observation(anchor=anchor, pinch_index=pinch, detected=True)


def _run(
    session: CalibrationSession,
    observations: list[Observation],
    *,
    fill: Observation | None = None,
) -> None:
    """Feed a series at a steady frame rate, then idle until the session's clock runs out.

    The padding matters: a user who finishes early keeps their hand in front of the camera while
    the countdown drains, and the derivation has to survive those extra frames.
    """
    now = 0.0
    for observation in observations:
        now += 1.0 / FPS
        session.observe(observation, now=now)

    padding = fill if fill is not None else observations[-1]
    while not session.finished:
        now += 1.0 / FPS
        session.observe(padding, now=now)


# ------------------------------------------------------------------------ neutral


def test_neutral_reports_the_middle_of_where_the_hand_actually_sat() -> None:
    session = CalibrationSession("neutral", settings=DEFAULTS, now=0.0)
    # A hand held "still" still wanders — this is what the trace from pass 8 looked like.
    wobble = [_hand((0.40 + 0.01 * (index % 3), 0.62 - 0.01 * (index % 2))) for index in range(90)]
    _run(session, wobble)

    result = session.result()
    assert result.state == "done"
    assert result.suggestion == {
        "cursor": {"centerX": pytest.approx(0.41, abs=0.02), "centerY": pytest.approx(0.615, abs=0.02)}
    }


def test_one_stray_frame_does_not_move_the_neutral_centre() -> None:
    """Median, not mean. A single mis-detected frame can land the anchor anywhere."""
    steady = [_hand((0.40, 0.60)) for _ in range(90)]
    clean = CalibrationSession("neutral", settings=DEFAULTS, now=0.0)
    _run(clean, list(steady))

    polluted = CalibrationSession("neutral", settings=DEFAULTS, now=0.0)
    _run(polluted, [*steady[:45], _hand((0.05, 0.98)), *steady[45:]])

    assert polluted.result().suggestion == clean.result().suggestion


def test_neutral_clamps_its_suggestion_into_the_range_the_engine_accepts() -> None:
    """A hand parked at the frame edge is a real measurement, but not a settable centre."""
    session = CalibrationSession("neutral", settings=DEFAULTS, now=0.0)
    _run(session, [_hand((0.05, 0.5)) for _ in range(90)])

    result = session.result()
    assert result.state == "done"
    assert result.suggestion is not None
    assert result.suggestion["cursor"]["centerX"] == pytest.approx(0.2)
    # The raw number is still reported, so the clamp is visible rather than silent.
    assert result.measurement is not None
    assert result.measurement["centerX"] == pytest.approx(0.05)


# -------------------------------------------------------------------------- reach


def _sweep(amplitude: float, *, center: float = 0.5) -> list[Observation]:
    """A triangle wave across the whole step, which samples the reach uniformly.

    Sized to the step's own duration: a session stops accepting observations the moment its clock
    runs out, so a longer series would quietly measure only its own beginning.
    """
    count = int(STEP_SECONDS["reach"] * FPS)
    observations: list[Observation] = []
    for index in range(count):
        phase = 4.0 * index / count
        offset = phase if phase <= 1 else (2.0 - phase if phase <= 3 else phase - 4.0)
        observations.append(_hand((center + amplitude * offset, 0.5)))
    return observations


def test_reach_sizes_the_area_to_cover_the_furthest_the_hand_went() -> None:
    """Coverage is a width, and the width has to span the reach on *both* sides of the centre."""
    settings = replace(DEFAULTS, cursor=replace(DEFAULTS.cursor, center_x=0.5))
    session = CalibrationSession("reach", settings=settings, now=0.0)
    _run(session, _sweep(0.25))

    result = session.result()
    assert result.state == "done"
    assert result.suggestion is not None
    # Reaching 0.25 either side of the centre needs a region 0.5 wide.
    assert result.suggestion["cursor"]["coverage"] == pytest.approx(0.5, abs=0.03)
    assert "centerX" not in result.suggestion["cursor"], "reach sizes the area, it does not move it"


def test_a_single_overshoot_does_not_size_the_whole_active_area() -> None:
    """Percentiles, not min/max. One frame at the frame edge would halve the sensitivity."""
    settings = replace(DEFAULTS, cursor=replace(DEFAULTS.cursor, center_x=0.5))
    sweep = _sweep(0.2)

    clean = CalibrationSession("reach", settings=settings, now=0.0)
    _run(clean, list(sweep))

    spiked = CalibrationSession("reach", settings=settings, now=0.0)
    _run(spiked, [*sweep[:90], _hand((0.99, 0.5)), *sweep[90:]])

    clean_suggestion = clean.result().suggestion
    spiked_suggestion = spiked.result().suggestion
    assert clean_suggestion is not None and spiked_suggestion is not None
    assert spiked_suggestion["cursor"]["coverage"] == pytest.approx(
        clean_suggestion["cursor"]["coverage"], abs=0.05
    )


# -------------------------------------------------------------------------- pinch


def _pinch_series(minima: list[float], *, open_level: float = 0.95) -> list[Observation]:
    """An open hand that closes and reopens once per entry in `minima`."""
    values: list[float] = [open_level] * 20
    for minimum in minima:
        midpoint = (open_level + minimum) / 2.0
        values += [midpoint, *([minimum] * 6), midpoint]
        values += [open_level] * 20
    return [_hand(pinch=value) for value in values]


def test_the_threshold_follows_the_worst_pinch_not_the_best() -> None:
    """A threshold tuned to the deepest attempt would miss the other two — the lost-click bug."""
    session = CalibrationSession("pinch", settings=DEFAULTS, now=0.0)
    _run(session, _pinch_series([0.30, 0.38, 0.34]), fill=_hand())

    result = session.result()
    assert result.state == "done", result.reason
    assert result.measurement is not None
    assert result.measurement["attempts"] == 3
    assert result.measurement["worstPinch"] == pytest.approx(0.38)

    assert result.suggestion is not None
    assert result.suggestion["gesture"]["pinchClose"] > 0.38
    # Hysteresis width is the user's, not ours: it is preserved across the suggestion.
    band = result.suggestion["gesture"]["pinchOpen"] - result.suggestion["gesture"]["pinchClose"]
    assert band == pytest.approx(DEFAULTS.gesture.pinch_open - DEFAULTS.gesture.pinch_close)


def test_a_wide_gap_puts_the_threshold_in_it_rather_than_beside_the_pinch() -> None:
    """A real measurement, kept as the regression it produced.

    Taken from a human hand on 2026-08-08: three pinches bottoming out at 0.16–0.18 against a hand
    resting at 1.16. The first version added a flat 0.08 and suggested **0.26** — a threshold
    hugging the pinch side of a gap 0.98 wide, which is the pass-10 bug in a new place. Eight
    seconds of deliberate pinches only ever contains clean ones; the clicks that go missing are the
    ones where an occluded thumb pushes the estimate up, and the width of the gap is the only
    evidence about how much room that needs.
    """
    session = CalibrationSession("pinch", settings=DEFAULTS, now=0.0)
    _run(session, _pinch_series([0.16, 0.18, 0.17], open_level=1.16), fill=_hand(pinch=1.16))

    result = session.result()
    assert result.state == "done", result.reason
    assert result.measurement == {
        "restingLevel": pytest.approx(1.16),
        "attempts": 3,
        "worstPinch": pytest.approx(0.18),
        "bestPinch": pytest.approx(0.16),
        # This hand never closed during the fist phase, so nothing bounds the threshold from
        # nearby and the open hand is the only ceiling left. See the test below for the opposite.
        "fistFloor": pytest.approx(1.16),
    }

    close = result.suggestion["gesture"]["pinchClose"]  # type: ignore[index]
    assert close > 0.40, "must not hug the pinch side of a gap this wide"
    assert close < 1.01, "and must stay clear of the resting hand"


def _then_fist(pinches: list[Observation], level: float) -> list[Observation]:
    """Pad the pinch attempts out to the phase boundary, then close the hand.

    The split is by the clock, not by the data, so a test that simply appends its fist frames puts
    them inside the pinch window and measures neither thing. Padding here is what makes the
    boundary in `PINCH_PHASE_SECONDS` the boundary the test actually exercises.
    """
    phase_frames = int(FPS * PINCH_PHASE_SECONDS)
    assert len(pinches) <= phase_frames, "the attempts do not fit in the pinch phase"
    padded = pinches + [pinches[0]] * (phase_frames - len(pinches))
    return padded + [_hand(pinch=level) for _ in range(int(FPS * 4.0))]


def test_the_closed_hand_is_what_bounds_the_threshold_not_the_open_one() -> None:
    """The measurement that produced this, kept as the regression it caused.

    Recorded from a human hand on 2026-08-09: pinches bottoming out near 0.10 against a hand
    resting at 1.16 — and a **fist reaching 0.195**. Measuring the gap up to the resting level put
    the threshold at 0.4695, more than twice the fist floor. On that same recording, at that
    threshold: closing the hand fired five drags, five of eight left clicks became drags because
    the loose threshold held them past `hold_to_drag_seconds`, and every single right click became
    nothing at all.

    The resting level is empty space. The fist is the nearest pose that can be confused with a
    pinch, and it is the only ceiling that was ever binding.
    """
    session = CalibrationSession("pinch", settings=DEFAULTS, now=0.0)
    _run(
        session,
        _then_fist(_pinch_series([0.10, 0.12, 0.11], open_level=1.16), 0.195),
        fill=_hand(pinch=0.195),
    )

    result = session.result()
    assert result.state == "done", result.reason
    assert result.measurement is not None
    assert result.measurement["fistFloor"] == pytest.approx(0.195, abs=0.01)

    close = result.suggestion["gesture"]["pinchClose"]  # type: ignore[index]
    assert close < 0.195, "a threshold above the fist floor makes closing your hand a click"
    assert close > 0.12, "and it still has to catch the weakest pinch that was made"


def test_a_fist_as_deep_as_the_pinch_is_refused_rather_than_guessed_at() -> None:
    """No threshold separates them, so there is no threshold to suggest.

    Refusing costs the user a retry. Suggesting one costs them a click every time they close their
    hand, landing wherever the cursor happened to be.
    """
    session = CalibrationSession("pinch", settings=DEFAULTS, now=0.0)
    _run(
        session,
        _then_fist(_pinch_series([0.20, 0.22, 0.21], open_level=1.16), 0.21),
        fill=_hand(pinch=0.21),
    )

    result = session.result()
    assert result.state == "failed"
    assert result.suggestion is None
    assert result.reason is not None and "clos" in result.reason.lower()


def test_one_pinch_is_not_enough_to_conclude_anything() -> None:
    session = CalibrationSession("pinch", settings=DEFAULTS, now=0.0)
    _run(session, _pinch_series([0.30]), fill=_hand())

    result = session.result()
    assert result.state == "failed"
    assert result.suggestion is None
    assert result.reason is not None and str(MIN_PINCH_ATTEMPTS) in result.reason


def test_a_threshold_that_would_collide_with_an_open_hand_is_refused() -> None:
    """The pass-10 lesson as a test.

    A hand that never fully opens — or a thumb estimate so poor that a "pinch" reads barely below
    resting — produces a threshold that fires on an open hand. Every one of those is a phantom
    click, which is a worse failure than the missed click this whole step exists to fix.
    """
    session = CalibrationSession("pinch", settings=DEFAULTS, now=0.0)
    _run(session, _pinch_series([0.70, 0.74, 0.72], open_level=0.95), fill=_hand())

    result = session.result()
    assert result.state == "failed"
    assert result.suggestion is None
    assert result.reason is not None and "open" in result.reason.lower()


# ----------------------------------------------------------------- shared behaviour


@pytest.mark.parametrize(
    ("step", "observations"),
    [
        ("neutral", [_hand((0.42, 0.58)) for _ in range(90)]),
        ("reach", _sweep(0.2)),
        ("pinch", _pinch_series([0.30, 0.38, 0.34])),
    ],
)
def test_every_suggestion_is_a_patch_the_engine_will_accept(
    step: str, observations: list[Observation]
) -> None:
    """The property the whole design rests on.

    A suggestion is shaped as a `set_settings` patch, so applying one is the ordinary path with
    the ordinary validation. If a derivation could ever produce a value outside the declared
    bounds, the user would be shown a measurement and then an error when they accepted it.
    """
    session = CalibrationSession(step, settings=DEFAULTS, now=0.0)  # type: ignore[arg-type]
    _run(session, list(observations), fill=_hand())

    suggestion = session.result().suggestion
    assert suggestion is not None
    merge(DEFAULTS, suggestion)  # raises InvalidSettings if the derivation ever drifts out of range


def test_losing_the_hand_for_too_long_ends_the_session_early() -> None:
    """Fails at the moment it goes wrong, not after the countdown drains — the user is standing
    there waiting, and a session that cannot succeed should say so."""
    session = CalibrationSession("pinch", settings=DEFAULTS, now=0.0)

    now = 0.0
    for _ in range(30):
        now += 1.0 / FPS
        session.observe(_hand(), now=now)
    assert not session.finished

    while not session.finished and now < STEP_SECONDS["pinch"]:
        now += 1.0 / FPS
        session.observe(NO_HAND, now=now)

    assert session.finished
    assert now < STEP_SECONDS["pinch"], "should have given up before the clock ran out"
    result = session.result()
    assert result.state == "failed"
    assert result.reason is not None and "hand" in result.reason.lower()


def test_a_hand_that_leaves_the_frame_at_the_edge_of_a_sweep_does_not_fail_the_step() -> None:
    """The `reach` step asks the user to go as far as is comfortable, so briefly leaving the frame
    is the instruction being followed, not a failure. A one-second rule failed here."""
    settings = replace(DEFAULTS, cursor=replace(DEFAULTS.cursor, center_x=0.5))
    session = CalibrationSession("reach", settings=settings, now=0.0)

    sweep = _sweep(0.2)
    gone = [NO_HAND] * int(1.5 * FPS)
    _run(session, [*sweep[:60], *gone, *sweep[60:]], fill=_hand((0.5, 0.5)))

    assert session.result().state == "done"


def test_a_brief_dropout_is_survivable() -> None:
    """Detection drops out constantly; only a sustained loss means the user walked away."""
    session = CalibrationSession("neutral", settings=DEFAULTS, now=0.0)
    steady = [_hand((0.40, 0.60)) for _ in range(40)]
    _run(session, [*steady, NO_HAND, NO_HAND, NO_HAND, *steady], fill=_hand((0.40, 0.60)))

    assert session.result().state == "done"


def test_progress_is_reportable_while_sampling() -> None:
    session = CalibrationSession("pinch", settings=DEFAULTS, now=0.0)
    session.observe(_hand(), now=1.0)

    result = session.result()
    assert result.state == "sampling"
    assert result.samples == 1
    assert result.seconds_remaining == pytest.approx(STEP_SECONDS["pinch"] - 1.0)
    assert result.suggestion is None
    # The total travels too, so a progress bar needs no second copy of STEP_SECONDS.
    assert result.seconds_total == pytest.approx(STEP_SECONDS["pinch"])


def test_an_unknown_step_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError):
        CalibrationSession("elbow", settings=DEFAULTS, now=0.0)  # type: ignore[arg-type]
