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

    def to_message(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "loaded": self.loaded,
            "stale": self.stale,
            "reason": self.reason,
            "calibrated": self.calibrated,
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

    variant = stored.get("modelVariant")
    version = stored.get("modelVersion")
    if variant != MODEL_VARIANT or version != MODEL_VERSION:
        return LoadedProfile(
            DEFAULTS,
            resolved,
            loaded=False,
            stale=True,
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
            DEFAULTS, resolved, loaded=False, stale=True,
            reason=f"Profile contains a value this engine will not accept: {exc}",
        )

    log.info("Loaded calibration profile from %s", resolved)
    return LoadedProfile(
        settings, resolved, loaded=True, calibrated=bool(stored.get("calibrated", False))
    )


def save_profile(
    settings: EngineSettings, path: Path | None = None, *, calibrated: bool = False
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
    """
    resolved = path or default_profile_path()
    payload = {
        "profileVersion": PROFILE_VERSION,
        "modelVariant": MODEL_VARIANT,
        "modelVersion": MODEL_VERSION,
        "calibrated": calibrated,
        "settings": to_values(settings),
    }
    try:
        atomic_write_json(resolved, payload)
    except OSError as exc:
        log.warning("Could not save the calibration profile to %s: %s", resolved, exc)
        return f"Settings are active for this session but could not be saved: {exc}"
    return None
