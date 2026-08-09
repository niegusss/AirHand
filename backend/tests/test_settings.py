"""Tunable settings — merging, bounds and refusal.

Almost every test here is about **refusing** something. These values arrive from a client over a
socket and end up steering OS input, so the interesting behaviour is not "a valid patch applies"
but "an invalid one changes nothing at all". Half-applying a patch would leave the engine in a
state no client asked for, with no way for the client to know which half landed.
"""

from __future__ import annotations

import dataclasses

import pytest

from airhand.settings import (
    DEFAULTS,
    KNOBS,
    EngineSettings,
    InvalidSettings,
    merge,
    settings_message,
    to_values,
)


def test_a_patch_only_changes_what_it_names() -> None:
    result = merge(DEFAULTS, {"gesture": {"pinchClose": 0.30}})

    assert result.gesture.pinch_close == pytest.approx(0.30)
    assert result.gesture.pinch_open == DEFAULTS.gesture.pinch_open
    assert result.pointer == DEFAULTS.pointer
    assert result.cursor == DEFAULTS.cursor


def test_sections_can_be_patched_together() -> None:
    result = merge(
        DEFAULTS,
        {"pointer": {"minCutoff": 0.5, "holdOnPinch": False}, "cursor": {"coverage": 0.85}},
    )

    assert result.pointer.min_cutoff == pytest.approx(0.5)
    assert result.pointer.hold_on_pinch is False
    assert result.cursor.coverage == pytest.approx(0.85)


def test_an_empty_patch_is_a_no_op() -> None:
    assert merge(DEFAULTS, {}) is DEFAULTS


def test_reset_returns_the_built_in_defaults() -> None:
    changed = merge(DEFAULTS, {"cursor": {"coverage": 0.9}})
    assert merge(changed, {"reset": True}) == DEFAULTS


def test_the_type_field_is_ignored_rather_than_rejected() -> None:
    """The patch arrives as a whole message, envelope included."""
    result = merge(DEFAULTS, {"type": "set_settings", "cursor": {"coverage": 0.5}})
    assert result.cursor.coverage == pytest.approx(0.5)


# ------------------------------------------------------------------------ refusal


def test_an_unknown_section_is_refused() -> None:
    with pytest.raises(InvalidSettings, match="section"):
        merge(DEFAULTS, {"camera": {"width": 1280}})


def test_an_unknown_knob_is_refused() -> None:
    with pytest.raises(InvalidSettings, match="pinchWobble"):
        merge(DEFAULTS, {"gesture": {"pinchWobble": 0.5}})


def test_a_value_outside_its_range_is_refused() -> None:
    with pytest.raises(InvalidSettings, match="between"):
        merge(DEFAULTS, {"pointer": {"beta": 500.0}})


def test_a_coverage_that_would_make_the_cursor_unusable_is_refused() -> None:
    """The lower bound is 0.2, not "greater than zero".

    A tiny active area multiplies hand tremor by the ratio of screen width to that area. The result
    is a pointer too twitchy to click the window in which you would undo the setting — and the only
    thing left is the kill-switch.
    """
    with pytest.raises(InvalidSettings):
        merge(DEFAULTS, {"cursor": {"coverage": 0.01}})


def test_a_string_where_a_number_belongs_is_refused() -> None:
    with pytest.raises(InvalidSettings, match="number"):
        merge(DEFAULTS, {"cursor": {"coverage": "0.5"}})


def test_a_boolean_is_not_accepted_as_a_number() -> None:
    """`bool` subclasses `int` in Python, so True would otherwise sail through as 1.0."""
    with pytest.raises(InvalidSettings, match="number"):
        merge(DEFAULTS, {"pointer": {"beta": True}})


def test_a_number_is_not_accepted_as_a_boolean() -> None:
    with pytest.raises(InvalidSettings, match="boolean"):
        merge(DEFAULTS, {"pointer": {"holdOnPinch": 1}})


def test_a_section_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(InvalidSettings, match="object"):
        merge(DEFAULTS, {"gesture": 0.5})


def test_a_patch_that_breaks_hysteresis_is_refused() -> None:
    """Range checks cannot catch a relationship *between* fields — the dataclass does that."""
    # Derived from the default, not written in. A literal stops testing anything the day the
    # default drops below it, and says nothing while it silently passes — which is what happened
    # when `pinchClose` moved 0.50 → 0.18 on 2026-08-09.
    below_close = round(DEFAULTS.gesture.pinch_close - 0.05, 4)

    with pytest.raises(InvalidSettings, match="hysteresis"):
        merge(DEFAULTS, {"gesture": {"pinchOpen": below_close}})


def test_a_refused_patch_leaves_the_current_settings_untouched() -> None:
    current = merge(DEFAULTS, {"cursor": {"coverage": 0.85}})

    with pytest.raises(InvalidSettings):
        merge(current, {"cursor": {"coverage": 0.9}, "pointer": {"beta": -1.0}})

    assert current.cursor.coverage == pytest.approx(0.85), "settings are immutable; nothing partial"


# ------------------------------------------------------------------------- message


def test_every_settable_field_has_a_declared_knob() -> None:
    """A new tuning field must not be able to reach the wire without bounds.

    Walking the dataclasses rather than listing names: a list would be the second copy this module
    exists to prevent, and it would silently fall behind the first time someone adds a knob.
    """
    for section in KNOBS:
        declared = {knob.attribute for knob in KNOBS[section]}
        actual = {field.name for field in dataclasses.fields(getattr(DEFAULTS, section))}
        assert declared == actual, f"{section}: {actual ^ declared} missing from KNOBS"


def test_the_message_carries_values_bounds_and_defaults() -> None:
    """All three ship together so a client never keeps its own copy of the ranges."""
    settings = merge(DEFAULTS, {"cursor": {"coverage": 0.9}})
    message = settings_message(settings)

    assert message["type"] == "settings"
    assert message["cursor"]["coverage"] == pytest.approx(0.9)
    assert message["defaults"]["cursor"]["coverage"] == pytest.approx(DEFAULTS.cursor.coverage)
    assert message["bounds"]["cursor"]["coverage"] == [0.2, 1.0]


def test_boolean_knobs_report_no_bounds() -> None:
    assert settings_message(DEFAULTS)["bounds"]["pointer"]["holdOnPinch"] is None


# ------------------------------------------------------------------- cli precedence


def _resolved(argv: list[str]):
    """Run the CLI's own resolution, so the test covers the path the engine actually takes."""
    from airhand.main import _resolve_settings, build_parser

    return _resolve_settings(build_parser().parse_args(argv))


def test_an_explicit_flag_beats_the_saved_profile(tmp_path) -> None:
    """A flag that silently does nothing because a profile exists is an hour of debugging."""
    from airhand.profile import save_profile

    profile_path = tmp_path / "profile.json"
    save_profile(merge(DEFAULTS, {"cursor": {"coverage": 0.8}}), profile_path)

    settings, profile = _resolved(["--profile", str(profile_path), "--cursor-coverage", "0.5"])

    assert settings.cursor.coverage == pytest.approx(0.5)
    assert profile is not None
    # The override is for this run only — it must not rewrite what the user calibrated.
    assert profile.settings.cursor.coverage == pytest.approx(0.8)


def test_the_profile_wins_over_the_built_in_default(tmp_path) -> None:
    from airhand.profile import save_profile

    profile_path = tmp_path / "profile.json"
    save_profile(merge(DEFAULTS, {"cursor": {"coverage": 0.8}}), profile_path)

    settings, _ = _resolved(["--profile", str(profile_path)])
    assert settings.cursor.coverage == pytest.approx(0.8)


def test_no_profile_ignores_the_disk_entirely(tmp_path) -> None:
    """Benchmarks and reproducible runs must not depend on the state of this machine."""
    from airhand.profile import save_profile

    profile_path = tmp_path / "profile.json"
    save_profile(merge(DEFAULTS, {"cursor": {"coverage": 0.8}}), profile_path)

    settings, profile = _resolved(["--profile", str(profile_path), "--no-profile"])

    assert settings == DEFAULTS
    assert profile is None, "nothing to persist into, so nothing gets written"


def test_a_flag_outside_its_range_is_refused_at_startup() -> None:
    with pytest.raises(InvalidSettings):
        _resolved(["--no-profile", "--cursor-coverage", "0.01"])


def test_values_round_trip_through_a_patch() -> None:
    """What the engine reports must be accepted back verbatim.

    Guards the naming seam: the wire is camelCase and the dataclasses are snake_case, so a typo in
    one direction would only show up as a setting that silently refuses to come back.
    """
    settings = EngineSettings()
    assert merge(DEFAULTS, to_values(settings)) == settings
