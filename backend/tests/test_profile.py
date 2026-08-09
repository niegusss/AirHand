"""Calibration profile persistence.

Everything here is about a profile that is *wrong* in some way, because those are the cases that
decide whether the engine still starts. A profile exists to make the engine nicer to use; one that
cannot be understood must never make it harder to run.

The model stamp is the load-bearing rule: every threshold is expressed in terms of MediaPipe's
landmark placement, so a profile written under a different model is not evidence about this one.
"""

from __future__ import annotations

import json

import pytest

from airhand.model import MODEL_VARIANT, MODEL_VERSION
from airhand.profile import PROFILE_VERSION, load_profile, save_profile
from airhand.settings import DEFAULTS, merge


@pytest.fixture
def profile_path(tmp_path):
    return tmp_path / "profile.json"


def test_settings_survive_a_round_trip(profile_path) -> None:
    settings = merge(DEFAULTS, {"cursor": {"coverage": 0.85}, "pointer": {"beta": 4.0}})

    assert save_profile(settings, profile_path) is None
    loaded = load_profile(profile_path)

    assert loaded.loaded is True
    assert loaded.stale is False
    assert loaded.settings == settings


def test_a_missing_profile_is_not_an_error(profile_path) -> None:
    """First launch. Defaults, no complaint, nothing to tell the user about."""
    loaded = load_profile(profile_path)

    assert loaded.settings == DEFAULTS
    assert (loaded.loaded, loaded.stale, loaded.reason) == (False, False, None)


def test_a_corrupt_profile_falls_back_without_raising(profile_path) -> None:
    """A crash mid-write, or a hand edit. The engine must still start."""
    profile_path.write_text("{ this is not json", encoding="utf-8")
    loaded = load_profile(profile_path)

    assert loaded.settings == DEFAULTS
    assert loaded.stale is True
    assert loaded.reason


def test_a_profile_saved_by_a_windows_editor_still_loads(profile_path) -> None:
    """A byte order mark must not read as "this file is not JSON".

    The profile is documented as inspectable, and Notepad and `Out-File -Encoding utf8` both add
    one. Found while verifying this feature by hand — the mangled file was refused safely, but a
    user would have experienced it as their calibration resetting because they opened it.
    """
    settings = merge(DEFAULTS, {"cursor": {"coverage": 0.75}})
    save_profile(settings, profile_path)
    profile_path.write_text(
        "﻿" + profile_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    loaded = load_profile(profile_path)
    assert loaded.loaded is True
    assert loaded.settings == settings


def test_a_profile_from_another_model_is_refused(profile_path) -> None:
    """The rule the whole stamp exists for.

    Thresholds are multiples of hand scale as *this* model places landmarks. Silently applying a
    profile calibrated against another one would be an invisible inconsistency — the numbers would
    look right in the UI and behave wrong in the hand.
    """
    save_profile(merge(DEFAULTS, {"cursor": {"coverage": 0.9}}), profile_path)
    stored = json.loads(profile_path.read_text(encoding="utf-8"))
    stored["modelVariant"] = "lite"
    profile_path.write_text(json.dumps(stored), encoding="utf-8")

    loaded = load_profile(profile_path)

    assert loaded.settings == DEFAULTS
    assert loaded.loaded is False
    assert loaded.stale is True
    assert "re-calibrate" in (loaded.reason or "")


def test_a_profile_in_an_older_format_is_refused(profile_path) -> None:
    save_profile(DEFAULTS, profile_path)
    stored = json.loads(profile_path.read_text(encoding="utf-8"))
    stored["profileVersion"] = PROFILE_VERSION - 1
    profile_path.write_text(json.dumps(stored), encoding="utf-8")

    loaded = load_profile(profile_path)
    assert loaded.stale is True
    assert loaded.settings == DEFAULTS


def test_a_profile_holding_an_illegal_value_is_refused(profile_path) -> None:
    """A downgrade, or a hand edit. Validation happens on load, not only on the wire."""
    profile_path.write_text(
        json.dumps(
            {
                "profileVersion": PROFILE_VERSION,
                "modelVariant": MODEL_VARIANT,
                "modelVersion": MODEL_VERSION,
                "settings": {"cursor": {"coverage": 0.001}},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_profile(profile_path)
    assert loaded.settings == DEFAULTS
    assert loaded.stale is True


def test_the_stamp_is_written_from_the_running_model(profile_path) -> None:
    save_profile(DEFAULTS, profile_path)
    stored = json.loads(profile_path.read_text(encoding="utf-8"))

    assert stored["modelVariant"] == MODEL_VARIANT
    assert stored["modelVersion"] == MODEL_VERSION


def test_a_failed_write_reports_a_reason_instead_of_raising(tmp_path) -> None:
    """The setting is already live when this runs.

    Refusing a working adjustment because the disk is full would be the wrong trade: losing
    persistence is recoverable and explicable, losing the adjustment is neither.
    """
    # A directory where the file should be: the write fails, nothing else does.
    blocked = tmp_path / "profile.json"
    blocked.mkdir()

    reason = save_profile(DEFAULTS, blocked)
    assert reason and "could not be saved" in reason


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path) -> None:
    blocked = tmp_path / "profile.json"
    blocked.mkdir()

    save_profile(DEFAULTS, blocked)

    strays = [entry.name for entry in tmp_path.iterdir() if entry.name.endswith(".tmp")]
    assert strays == [], f"temp files left behind: {strays}"


def test_the_profile_is_not_the_handshake(tmp_path) -> None:
    """Different lifetimes, so they must not be the same file.

    The handshake is deleted on clean shutdown; the profile has to survive one. Sharing a file
    would show up much later as "my calibration disappears when I close the app".
    """
    from airhand.handshake import default_handshake_path
    from airhand.profile import default_profile_path

    assert default_profile_path() != default_handshake_path()
    assert default_profile_path().parent == default_handshake_path().parent
