"""Calibration profile — the engine's own persistence.

The profile belongs to the engine, not to the UI. `python -m airhand.main` has to keep working on
its own, and a user who runs the engine standalone must still get the calibration they set: putting
this in the desktop app's storage would quietly break that.

It lives beside the handshake file but is a separate file on purpose. The handshake is deleted on
clean shutdown; the profile has to survive one. Merging them would surface later as "my calibration
disappears every time I close the app".

**Every profile carries the model it was made against.** MediaPipe's landmark placement is what
every threshold here is expressed in terms of, so a profile written under a different model is not
evidence about this one. On a mismatch the profile is *not* applied — defaults stand and the reason
travels to the UI, which can then ask for a re-calibration. Applying it silently is the kind of
invisible inconsistency `systemPatterns.md` rules out.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .handshake import app_data_dir
from .jsonfile import atomic_write_json, read_json
# From `model`, not `tracking`: importing the tracking package pulls in MediaPipe, and a profile
# must be readable on a machine running `--source synthetic` with no CV stack installed.
from .model import MODEL_VARIANT, MODEL_VERSION
from .settings import DEFAULTS, EngineSettings, InvalidSettings, merge, to_values

log = logging.getLogger(__name__)

PROFILE_FILENAME = "profile.json"

# Bumped only when the stored shape changes incompatibly. A mismatch falls back to defaults rather
# than migrating — there is nothing worth migrating yet, and a wrong migration is worse than a
# re-calibration.
PROFILE_VERSION = 1


def default_profile_path() -> Path:
    return app_data_dir() / PROFILE_FILENAME


def now_stamp() -> str:
    """Current local time with its UTC offset, to the second.

    Public so a caller that has to *report* the save time can generate it once and hand the same
    string to :func:`save_profile`. Reading the clock twice would let the file and the message the
    UI receives disagree by a second, which is a small lie about the same event.

    Seconds rather than microseconds: this is a human-facing record of when settings last changed,
    and six decimal places imply a precision the answer does not have.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class LoadedProfile:
    """What was loaded, and the honest story about it for the UI."""

    settings: EngineSettings
    path: Path
    loaded: bool
    """True when a profile exists but was refused — the UI should offer a re-calibration rather
    than silently showing defaults as if nothing had been saved."""
    stale: bool = False
    reason: str | None = None
    """Whether the user has actually been through the Calibration wizard.

    Deliberately not the same thing as `loaded`. Every settings change writes this file, so a
    profile exists the moment anyone nudges a slider — which would satisfy a "must calibrate on
    first run" check without anyone having calibrated. Only `calibrate/complete` sets this.
    """
    calibrated: bool = False
    """When this file was last written, ISO 8601 with a UTC offset. None for a profile that was
    never saved, or one written before the field existed.

    Stored in the file rather than read from its filesystem timestamp: an mtime is reset by copying,
    syncing or restoring the file, and the question this answers — "when did these numbers last
    change?" — has to survive all three.
    """
    saved_at: str | None = None
    """Which capture device the user chose, or None if they never have.

    **Read even from a profile whose settings were refused.** Every threshold in `settings` is
    expressed in terms of the landmark placement of a particular MediaPipe model, which is why a
    model change throws them away — but a camera index is not a measurement of anything. It is a
    fact about this machine, and losing it to a model upgrade would be a decision nobody made.

    None rather than 0 for "never chosen": the engine's built-in default is also 0, and the two
    have to stay distinguishable or the UI cannot say whether a selection exists.
    """
    camera_index: int | None = None

    def to_message(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "loaded": self.loaded,
            "stale": self.stale,
            "reason": self.reason,
            "calibrated": self.calibrated,
            "savedAt": self.saved_at,
        }


def load_profile(path: Path | None = None) -> LoadedProfile:
    """Read the stored profile, or explain why the defaults are in force.

    Never raises. A profile that cannot be understood must not stop the engine from starting —
    the whole point of the file is to make the engine nicer to use, not harder to run.
    """
    resolved = path or default_profile_path()
    stored = read_json(resolved)

    if stored is None:
        reason = None if not resolved.exists() else "The profile file could not be read as JSON."
        return LoadedProfile(DEFAULTS, resolved, loaded=False, stale=reason is not None,
                             reason=reason)

    if int(stored.get("profileVersion", 0)) != PROFILE_VERSION:
        return LoadedProfile(
            DEFAULTS,
            resolved,
            loaded=False,
            stale=True,
            reason=(
                f"Profile was written in format v{stored.get('profileVersion')}, this engine "
                f"expects v{PROFILE_VERSION}. Defaults are in use."
            ),
        )

    # Read before the staleness checks and carried through every return below them. The checks that
    # follow reject *settings* — thresholds expressed in terms of a model's landmark placement — and
    # a capture device is not one of those. Throwing away which webcam the user picked because
    # MediaPipe shipped a new model would be a decision nobody made and nothing would explain.
    #
    # It is deliberately *not* read above the `profileVersion` check: there the file's shape itself
    # is untrusted, so a field found at that key means nothing.
    camera_index = _stored_camera_index(stored)

    variant = stored.get("modelVariant")
    version = stored.get("modelVersion")
    if variant != MODEL_VARIANT or version != MODEL_VERSION:
        return LoadedProfile(
            DEFAULTS,
            resolved,
            loaded=False,
            stale=True,
            camera_index=camera_index,
            reason=(
                f"Profile was calibrated against model {variant} {version}, this engine runs "
                f"{MODEL_VARIANT} {MODEL_VERSION}. Thresholds are expressed in terms of landmark "
                "placement, so the saved values are not evidence about this model — please "
                "re-calibrate."
            ),
        )

    try:
        settings = merge(DEFAULTS, stored.get("settings", {}))
    except InvalidSettings as exc:
        # A hand-edited or downgraded profile. Same treatment as a corrupt one.
        return LoadedProfile(
            DEFAULTS, resolved, loaded=False, stale=True, camera_index=camera_index,
            reason=f"Profile contains a value this engine will not accept: {exc}",
        )

    log.info("Loaded calibration profile from %s", resolved)
    saved_at = stored.get("savedAt")
    return LoadedProfile(
        settings,
        resolved,
        loaded=True,
        camera_index=camera_index,
        calibrated=bool(stored.get("calibrated", False)),
        # Read back as whatever is there. Profiles written before this field existed simply have
        # none, and that is a true statement about them — inventing a date would not be.
        saved_at=saved_at if isinstance(saved_at, str) else None,
    )


def _stored_camera_index(stored: dict[str, Any]) -> int | None:
    """The saved device index, or None if there is not a usable one.

    Hand-editing this file is documented as supported, so a string, a float or a negative number
    has to degrade to "no selection" rather than reach the source. `bool` is excluded explicitly
    because it is a subclass of `int` and `True` would otherwise be read as camera 1.
    """
    value = stored.get("cameraIndex")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def save_profile(
    settings: EngineSettings,
    path: Path | None = None,
    *,
    calibrated: bool = False,
    saved_at: str | None = None,
    camera_index: int | None = None,
) -> str | None:
    """Persist the settings. Returns None on success, or a human-readable reason on failure.

    **A failed write does not undo the change.** The setting is already live for this session, and
    refusing a working adjustment because the disk is full or the directory is read-only would be
    the wrong trade — losing persistence is recoverable, losing the adjustment is annoying and
    inexplicable from the UI.

    `calibrated` is passed in on every write rather than merged from what is already on disk. The
    caller is the only thing that knows whether the wizard has been completed this session, and a
    read-modify-write here would silently resurrect the flag from a profile the engine had already
    refused as stale.

    **`camera_index` follows the same rule, and for a sharper reason.** Every write is a full
    rewrite, so a caller that forgets to pass it erases the user's device choice — and it would
    happen on the next slider nudge, nowhere near anything to do with cameras.
    """
    resolved = path or default_profile_path()
    payload = {
        "profileVersion": PROFILE_VERSION,
        "modelVariant": MODEL_VARIANT,
        "modelVersion": MODEL_VERSION,
        "calibrated": calibrated,
        # Beside `settings`, not inside it. The sections under `settings` are validated against
        # `KNOBS` and are discarded wholesale when the model changes; a device index belongs to
        # neither of those rules.
        "cameraIndex": camera_index,
        # Local time *with its offset*, not a bare local timestamp and not bare UTC. The offset
        # keeps it unambiguous, and local time is what the user recognises — this file is theirs,
        # read on the machine that wrote it, and "16:32+02:00" answers "was that before or after I
        # changed the threshold?" without anyone doing timezone arithmetic.
        "savedAt": saved_at or now_stamp(),
        "settings": to_values(settings),
    }
    try:
        atomic_write_json(resolved, payload)
    except OSError as exc:
        log.warning("Could not save the calibration profile to %s: %s", resolved, exc)
        return f"Settings are active for this session but could not be saved: {exc}"
    return None
