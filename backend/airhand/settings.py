"""Tunable engine settings — the one place bounds and defaults are defined.

Everything a user can adjust from the Calibration screen lives here: gesture thresholds, pointer
smoothing and cursor sensitivity. The CLI, the wire format, the validator and the saved profile all
read this module, so none of them can disagree about what a legal value is.

**Bounds and defaults go out on the wire** alongside the values, for the same reason `gestureDebug`
carries its own thresholds: a client that keeps its own copy of the ranges will eventually draw a
slider whose limits no longer match the engine's. The Calibration screen builds itself from what
arrives here.

**Nothing is clamped silently.** An out-of-range value is refused and the previous settings stand.
These numbers steer OS input, and a `coverage` of 0.01 produces a cursor too twitchy to click the
window in which you would fix `coverage` — so the lower bounds are narrow rather than merely
positive, and the kill-switch remains the backstop.

The message builder lives here rather than in `protocol.py` because `protocol.py` sits *below* the
gesture and pointer layers in the import graph — it defines the `Gesture` literal they depend on.
Moving this there would make the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .gestures import GestureConfig
from .pointer import PointerConfig

# Fraction of frame width used as the active area. Not in `PointerConfig` because it is a property
# of the mapping to the screen, not of smoothing.
DEFAULT_CURSOR_COVERAGE = 0.7


@dataclass(frozen=True)
class Knob:
    """One adjustable value: its wire name, its attribute, and what it may be.

    `low`/`high` are None for booleans, which have no range but still need to be declared so the
    validator knows the field exists at all.
    """

    wire: str
    attribute: str
    low: float | None = None
    high: float | None = None

    @property
    def is_boolean(self) -> bool:
        return self.low is None and self.high is None


# Ranges are deliberately generous enough to be useful and narrow enough that no value inside them
# produces an unusable pointer. Anything outside is refused, not clamped.
KNOBS: dict[str, tuple[Knob, ...]] = {
    "gesture": (
        Knob("pinchClose", "pinch_close", 0.05, 0.9),
        Knob("pinchOpen", "pinch_open", 0.1, 1.5),
        Knob("holdToDragSeconds", "hold_to_drag_seconds", 0.1, 2.0),
        Knob("clickLatchSeconds", "click_latch_seconds", 0.05, 1.0),
        Knob("extendedAngleDegrees", "extended_angle_degrees", 90.0, 180.0),
        Knob("scrollStep", "scroll_step", 0.05, 1.5),
        # Capped well below the click latch: a long grace would let the engine resolve a click
        # from a pinch the user had already given up on.
        Knob("dropoutGraceSeconds", "dropout_grace_seconds", 0.0, 0.5),
    ),
    "pointer": (
        Knob("minCutoff", "min_cutoff", 0.1, 5.0),
        Knob("beta", "beta", 0.0, 30.0),
        Knob("dCutoff", "d_cutoff", 0.1, 5.0),
        Knob("holdOnPinch", "hold_on_pinch"),
        Knob("dropoutGraceSeconds", "dropout_grace_seconds", 0.0, 1.0),
    ),
    "cursor": (
        # Lower bound is 0.2, not "greater than zero". A tiny active area multiplies hand tremor by
        # the ratio of screen width to that area, and the result is a cursor you cannot use to undo
        # the setting that caused it.
        Knob("coverage", "coverage", 0.2, 1.0),
        # 0.2..0.8 rather than the full frame: a centre near the edge is nearly all clamp, so the
        # region stops following the setting and the slider quietly stops doing anything.
        Knob("centerX", "center_x", 0.2, 0.8),
        Knob("centerY", "center_y", 0.2, 0.8),
    ),
}


@dataclass(frozen=True)
class CursorSettings:
    """How the hand's position maps onto the screen: how much of the frame, and centred where."""

    coverage: float = DEFAULT_CURSOR_COVERAGE
    """Where the active area sits in the frame. The default is the middle, which is what everyone
    got before the Calibration screen could measure a comfortable resting position — a user sitting
    to one side of the camera would otherwise have to reach across it to touch the screen edge."""
    center_x: float = 0.5
    center_y: float = 0.5


@dataclass(frozen=True)
class EngineSettings:
    """Everything the Calibration screen can change, in one immutable value."""

    gesture: GestureConfig = GestureConfig()
    pointer: PointerConfig = PointerConfig()
    cursor: CursorSettings = CursorSettings()


DEFAULTS = EngineSettings()

_SECTION_TYPES = {"gesture": GestureConfig, "pointer": PointerConfig, "cursor": CursorSettings}


class InvalidSettings(ValueError):
    """A patch that must be refused. Carries a message meant for the user, not a stack trace."""


def _knob(section: str, wire: str) -> Knob:
    for knob in KNOBS[section]:
        if knob.wire == wire:
            return knob
    known = ", ".join(item.wire for item in KNOBS[section])
    raise InvalidSettings(f"Unknown {section} setting {wire!r}. Known: {known}")


def bounds_for(section: str, wire: str) -> tuple[float, float] | None:
    """The legal range for one knob, or None if it is a boolean.

    Exposed so the calibration derivations can clamp their suggestions to what `merge` will accept
    without keeping a second copy of these numbers — the same reason `bounds` goes out on the wire.
    """
    knob = _knob(section, wire)
    if knob.is_boolean:
        return None
    assert knob.low is not None and knob.high is not None
    return knob.low, knob.high


def _check(section: str, wire: str, value: Any) -> Any:
    knob = _knob(section, wire)

    if knob.is_boolean:
        if not isinstance(value, bool):
            raise InvalidSettings(f"{section}.{wire} must be a boolean, got {value!r}")
        return value

    # `bool` is a subclass of `int`, so True would otherwise sail through as the number 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidSettings(f"{section}.{wire} must be a number, got {value!r}")

    number = float(value)
    assert knob.low is not None and knob.high is not None
    if not knob.low <= number <= knob.high:
        raise InvalidSettings(
            f"{section}.{wire} must be between {knob.low} and {knob.high}, got {number}"
        )
    return number


def merge(current: EngineSettings, patch: Mapping[str, Any]) -> EngineSettings:
    """Apply a partial patch, or raise :class:`InvalidSettings` and change nothing.

    The all-or-nothing behaviour is the point: applying the valid half of a patch would leave the
    engine in a state no client asked for, and the client would have no way to know which half
    landed.
    """
    if patch.get("reset"):
        return DEFAULTS

    checked: dict[str, dict[str, Any]] = {}
    for section, values in patch.items():
        if section in ("type", "reset"):
            continue
        if section not in KNOBS:
            raise InvalidSettings(
                f"Unknown settings section {section!r}. Known: {', '.join(KNOBS)}"
            )
        if not isinstance(values, Mapping):
            raise InvalidSettings(f"Section {section!r} must be an object, got {values!r}")

        section_updates = {
            _knob(section, wire).attribute: _check(section, wire, value)
            for wire, value in values.items()
        }
        if section_updates:
            checked[section] = section_updates

    if not checked:
        return current

    try:
        # Every dataclass is constructed inside this block, because each validates its own internal
        # consistency in __post_init__ — the pinch hysteresis gap, positive filter cutoffs. Those
        # are relationships *between* fields, which the per-field range checks above cannot see.
        updates = {
            section: replace(getattr(current, section), **values)
            for section, values in checked.items()
        }
        return replace(current, **updates)
    except ValueError as exc:
        raise InvalidSettings(str(exc)) from exc


def to_values(settings: EngineSettings) -> dict[str, dict[str, Any]]:
    """The settings as wire-shaped sections, without the envelope."""
    return {
        section: {knob.wire: getattr(getattr(settings, section), knob.attribute) for knob in knobs}
        for section, knobs in KNOBS.items()
    }


def _bounds() -> dict[str, dict[str, Any]]:
    return {
        section: {
            knob.wire: None if knob.is_boolean else [knob.low, knob.high] for knob in knobs
        }
        for section, knobs in KNOBS.items()
    }


def settings_message(
    settings: EngineSettings,
    *,
    profile: Mapping[str, Any] | None = None,
    active_area: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """The `settings` server→client message.

    `bounds` and `defaults` ride along on every send. They are static, so this is redundant on the
    wire — and worth it, because the alternative is a second copy of these numbers in TypeScript
    that nothing keeps in step with this file.

    `active_area` is the rectangle `coverage` and the centre actually produce, in normalized frame
    coordinates. It is passed in rather than computed here because deriving it needs the screen and
    the camera, neither of which this module knows about — and it travels at all so the Calibration
    screen can draw the reach box over the camera image without doing the mapping arithmetic
    itself. That arithmetic belongs to `cursor/mapping.py`; a copy of it in TypeScript would be
    computer-vision logic in the frontend, which this project does not allow.
    """
    message: dict[str, Any] = {"type": "settings"}
    message.update(to_values(settings))
    message["bounds"] = _bounds()
    message["defaults"] = to_values(DEFAULTS)
    message["profile"] = dict(profile) if profile else None
    message["activeArea"] = dict(active_area) if active_area else None
    return message
