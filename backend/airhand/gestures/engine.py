"""Gesture Engine — classifies Move / Left Click / Right Click / Drag / Scroll.

Reads camera-independent features and emits a gesture. It never touches the OS: turning a
classification into an actual click is the Cursor Engine's job, and keeping that separation is
what lets thresholds be tuned safely while nothing is actuating.

Two ideas carry most of the reliability:

**Hysteresis.** A pinch closes at one threshold and opens at a larger one. With a single threshold,
a hand hovering near it produces a burst of open/close transitions — which downstream would be a
burst of clicks.

**Time, not frames.** Every duration is in seconds. On a 60 fps camera a frame-counted hold would
trigger in half the wall-clock time of a 30 fps one, so the same gesture would mean different
things on different hardware.

Clicks are events, not poses, so they are latched briefly to stay visible to a UI that samples at
10 Hz. The Cursor Engine will consume state transitions directly instead of the sampled field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..protocol import Gesture
from .features import HandFeatures, extract


class MachineState(str, Enum):
    """Internal state. Richer than the public `Gesture` — it includes undecided states."""

    IDLE = "idle"
    MOVE = "move"
    """Thumb+index closed, not yet held long enough to be a drag."""
    PINCH_INDEX_PENDING = "pinch_index_pending"
    DRAG = "drag"
    """Thumb+middle closed, waiting to see if it is a right click."""
    PINCH_MIDDLE_PENDING = "pinch_middle_pending"
    SCROLL = "scroll"


# The undecided states: a pinch is closed but the release has not yet said whether it was a click
# or a hold. Everything that must behave differently "while a click may be happening" keys on this.
_PENDING_STATES = frozenset({MachineState.PINCH_INDEX_PENDING, MachineState.PINCH_MIDDLE_PENDING})


@dataclass(frozen=True)
class GestureConfig:
    """Thresholds, all in multiples of hand scale or in seconds.

    Calibration (FR-009, FR-010) will populate this from a saved profile; until then the defaults
    are starting points to be tuned against the live readouts in Diagnostics.
    """

    """Thumb-to-index distance, in multiples of hand scale, at which a pinch counts as closed.

    **Bounded by the nearest confusable pose, which is a closed hand — not by a resting one.**
    That distinction is the whole history of this value. It was set from a trace of an open hand
    that never read below 0.853, and a threshold of 0.50 looked like a comfortable margin under
    that. It is not a margin under anything that matters: when the hand curls, the thumb comes in
    beside the fingertips, and the fist is the pose a click has to be told apart from.

    Measured against a real closed hand for the first time on 2026-08-09
    (`tools/bench_poses.py`, 1622 frames): fist floor **0.195**, p02 0.215, against **0.103** for a
    real thumb-to-fingertip contact. At 0.50 that recording emits five events on the fist alone,
    including a drag. The synthetic fist in `handmodel` reproduces it independently at 0.425.

    0.18 sits under the measured floor with the whole contact range still below it. Deliberately
    biased low: a missed click costs a repeat, an unrequested one lands somewhere the user cannot
    explain. One hand, one camera — calibration is what fits this to a particular hand, and it is
    mandatory on first run.
    """
    pinch_close: float = 0.18
    """Must exceed `pinch_close` — the gap is the hysteresis band.

    Moved with `pinch_close` to keep the band at 0.20. Narrowing it would be the expensive mistake
    here: the band is the only thing standing between a hand resting near the threshold and a burst
    of phantom clicks, and per-frame noise in this measurement is around ±0.03, so 0.20 is roughly
    six times the noise. A fist still clears 0.38 comfortably.
    """
    pinch_open: float = 0.38
    """How long the **index** pinch must be held before it becomes a drag instead of a click.

    Applies to that pair only. The middle pair has no held mode — right-button dragging is not a
    gesture this project has — so a duration limit there would resolve a slow right click into
    nothing at all. That is exactly what it did until 2026-08-09, and the missing clicks looked
    enough like a threshold problem to get this value raised in compensation.

    Coupled to `pinch_close`: a looser threshold starts this timer while the fingers are still
    approaching, so the same physical click reads as a longer hold. Retune one, check the other.
    """
    hold_to_drag_seconds: float = 0.4
    click_latch_seconds: float = 0.2
    extended_angle_degrees: float = 150.0
    """Vertical hand travel that makes one scroll step, in multiples of hand scale.

    Expressed against hand scale like every other threshold, so scrolling feels the same at any
    distance from the lens and on any camera.
    """
    scroll_step: float = 0.25
    """How long detection may drop out before pinch state is thrown away.

    A pinch is the moment MediaPipe is most likely to lose the hand — the thumb disappears behind
    the index finger — and a single lost frame used to destroy the pending click outright, because
    a click is defined by the *release* and the release needs the engine to remember it was closed.

    Time the hand is invisible does not count toward `hold_to_drag_seconds`. See `_PinchTracker`.
    """
    dropout_grace_seconds: float = 0.15

    def __post_init__(self) -> None:
        if self.pinch_open <= self.pinch_close:
            raise ValueError(
                "pinch_open must exceed pinch_close; without a gap there is no hysteresis "
                "and a hand resting near the threshold produces a burst of phantom clicks."
            )
        if self.dropout_grace_seconds < 0.0:
            raise ValueError(
                f"dropout_grace_seconds must not be negative, got {self.dropout_grace_seconds}"
            )


class GestureEventType(str, Enum):
    """Discrete things that happened, as opposed to the pose that is currently held.

    The Cursor Engine consumes these, never the sampled `gesture` field: that field latches
    clicks for ~200 ms so a 10 Hz UI can see them, and a latch is not a click timestamp. Acting on
    it would fire one click per frame for the whole latch window.
    """

    LEFT_CLICK = "left_click"
    RIGHT_CLICK = "right_click"
    DRAG_START = "drag_start"
    DRAG_END = "drag_end"
    SCROLL = "scroll"


@dataclass(frozen=True)
class GestureEvent:
    type: GestureEventType
    """Scroll steps: positive is content moving up (wheel away from the user). Zero otherwise."""
    scroll_steps: int = 0


@dataclass(frozen=True)
class GestureDebug:
    """Everything needed to tune thresholds by looking at them."""

    state: str
    hand_scale: float
    pinch_index: float
    pinch_middle: float
    extended: dict[str, bool]
    angles: dict[str, float]
    pinch_close: float
    pinch_open: float

    def to_message(self) -> dict[str, object]:
        return {
            "state": self.state,
            "handScale": round(self.hand_scale, 4),
            "pinchIndex": round(self.pinch_index, 3),
            "pinchMiddle": round(self.pinch_middle, 3),
            "extended": dict(self.extended),
            "angles": {name: round(value, 1) for name, value in self.angles.items()},
            "thresholds": {
                "pinchClose": self.pinch_close,
                "pinchOpen": self.pinch_open,
            },
        }


@dataclass
class _PinchTracker:
    """Hysteresis plus press timing for one pinch pair."""

    closed: bool = False
    closed_at: float | None = None
    """Smallest distance seen since this pair closed; `inf` while open.

    How near the thumb actually came to *this* fingertip over the whole closure. Used to decide
    which pinch the user meant when both pairs are closed at once — see `GestureEngine._owner`.
    Comparing the two current distances frame by frame would switch owners mid-gesture on noise.
    """
    best: float = float("inf")

    def update(self, distance: float, now: float, config: GestureConfig) -> None:
        if self.closed:
            if distance > config.pinch_open:
                self.closed = False
                self.closed_at = None
                self.best = float("inf")
            else:
                self.best = min(self.best, distance)
        elif distance < config.pinch_close:
            self.closed = True
            self.closed_at = now
            self.best = distance

    def held_for(self, now: float) -> float:
        return 0.0 if self.closed_at is None else now - self.closed_at

    def skip(self, seconds: float) -> None:
        """Discount a stretch of time the hand could not be seen.

        Pushing the start of the hold forward stops an invisible gap from counting toward
        `hold_to_drag_seconds`. Without it, surviving a dropout would sometimes turn a short tap
        into a drag — trading a missing click for an unwanted one, which is the worse failure.
        """
        if self.closed_at is not None:
            self.closed_at += seconds

    def reset(self) -> None:
        self.closed = False
        self.closed_at = None
        self.best = float("inf")


@dataclass(frozen=True)
class GestureUpdate:
    """Result of one frame.

    `gesture` is for display and `events` are for actuation — they are deliberately different
    views. Returning a dataclass rather than a growing tuple keeps the next addition cheap.
    """

    gesture: Gesture
    events: tuple[GestureEvent, ...] = ()
    debug: GestureDebug | None = None
    """True while a pinch is closed but not yet resolved into a click or a drag.

    A statement about the *gesture* — "something that may become a click is under way" — not about
    the cursor. The pointer stage uses it to hold position so the click lands where the user aimed,
    but nothing here knows that; keeping the flag phrased this way is what stops cursor policy from
    leaking into the classifier.

    Deliberately false during a drag: a drag that cannot move cannot drag anything.
    """
    pointer_hold: bool = False


@dataclass
class GestureEngine:
    """Stateful classifier. One instance per tracked hand."""

    config: GestureConfig = field(default_factory=GestureConfig)

    _index: _PinchTracker = field(default_factory=_PinchTracker, init=False)
    _middle: _PinchTracker = field(default_factory=_PinchTracker, init=False)
    _state: MachineState = field(default=MachineState.IDLE, init=False)
    _latched: Gesture | None = field(default=None, init=False)
    _latched_until: float = field(default=0.0, init=False)
    _dragging: bool = field(default=False, init=False)
    _scroll_origin: float | None = field(default=None, init=False)
    _events: list[GestureEvent] = field(default_factory=list, init=False)
    """Which pinch pair owns the gesture currently under way — `"index"`, `"middle"` or None.

    **A hand has one thumb, so at most one pinch is being made.** The two pairs are nevertheless
    measured independently, and they are not independent: when the index finger is curled — which
    is what it does while the thumb reaches across to the middle finger — the two fingertips sit
    beside each other, so the thumb cannot approach one without approaching the other. Measured
    2026-08-09: a right click with the index curled reads `pinch_index` 0.264 against
    `pinch_middle` 0.150, and any threshold above 0.264 closes both.

    Without an owner, both pairs resolve on release and one gesture emits a left click *and* a
    right click, or a held right click starts a drag. Both were reachable at the shipped default.
    """
    _owner: str | None = field(default=None, init=False)
    """When the hand went missing, or None while it is visible. Drives the dropout grace."""
    _lost_since: float | None = field(default=None, init=False)

    def reset(self) -> tuple[GestureEvent, ...]:
        """Reset all state, emitting any event needed to leave the world consistent.

        A drag in progress must produce a `drag_end`: losing the hand mid-drag would otherwise
        leave the mouse button held down on the user's machine.
        """
        events: tuple[GestureEvent, ...] = ()
        if self._dragging:
            events = (GestureEvent(GestureEventType.DRAG_END),)

        self._index.reset()
        self._middle.reset()
        self._owner = None
        self._state = MachineState.IDLE
        self._latched = None
        self._latched_until = 0.0
        self._dragging = False
        self._scroll_origin = None
        self._events = []
        self._lost_since = None
        return events

    def update(
        self,
        landmarks: list[list[float]] | None,
        *,
        aspect: float,
        now: float,
    ) -> GestureUpdate:
        """Classify one frame. `now` is a monotonic clock in seconds."""
        features = None
        if landmarks is not None:
            features = extract(
                landmarks,
                aspect=aspect,
                extended_angle_degrees=self.config.extended_angle_degrees,
            )
        if features is None:
            return GestureUpdate("none", self._on_lost(now), None)

        self._events = []
        self._resume(now)
        gesture = self._classify(features, now)
        return GestureUpdate(
            gesture,
            tuple(self._events),
            self._debug(features),
            pointer_hold=self._state in _PENDING_STATES,
        )

    def _on_lost(self, now: float) -> tuple[GestureEvent, ...]:
        """The hand is not usable this frame. Decide between riding it out and starting over.

        **A drag ends immediately, grace or no grace.** Keeping a button held down on a hand nobody
        can see is the failure the whole safety model exists to prevent, and no amount of "it will
        probably come back" justifies it. The grace covers only the pinch bookkeeping needed to
        resolve a *click*, which cannot hurt anyone if it turns out to be wrong.

        The scroll origin goes too: a stale reference point would make the next visible frame look
        like a large jump and emit a burst of wheel steps.
        """
        if self._lost_since is None:
            self._lost_since = now
        self._scroll_origin = None

        if now - self._lost_since >= self.config.dropout_grace_seconds:
            # `reset` clears `_lost_since`, so restore it — the hand is still missing, and losing
            # the mark would restart the grace on every subsequent empty frame.
            events = self.reset()
            self._lost_since = now
            return events

        if self._dragging:
            self._dragging = False
            return (GestureEvent(GestureEventType.DRAG_END),)
        return ()

    def _resume(self, now: float) -> None:
        """The hand is back inside the grace window: discount the time it was invisible."""
        if self._lost_since is None:
            return
        gap = now - self._lost_since
        self._lost_since = None
        self._index.skip(gap)
        self._middle.skip(gap)

    def _emit(self, event_type: GestureEventType, *, scroll_steps: int = 0) -> None:
        self._events.append(GestureEvent(event_type, scroll_steps))

    def _elect_owner(self) -> None:
        """Decide which pinch pair the gesture under way belongs to.

        **Depth, not order.** The two distances cross `pinch_close` within a frame or two of each
        other, in an order that depends on the hand and on noise; the finger the thumb is actually
        touching is unambiguous for the whole closure. `best` is that, accumulated.

        **Frozen once a pair opens.** The pairs release at different times — the index sits further
        from the thumb during a middle pinch, so it crosses `pinch_open` first — and re-electing on
        the frames in between would hand the gesture to whichever pair happened to still be closed.
        That is how a right click would end up emitting a left click a few frames later.

        Ties go to the index, matching the precedence drag has always had: it is the only sustained
        mode, and interrupting one costs more than the reverse.
        """
        if self._index.closed and self._middle.closed:
            self._owner = "index" if self._index.best <= self._middle.best else "middle"
        elif self._owner is None:
            if self._index.closed:
                self._owner = "index"
            elif self._middle.closed:
                self._owner = "middle"

    def _classify(self, features: HandFeatures, now: float) -> Gesture:
        config = self.config

        was_index_closed = self._index.closed
        was_middle_closed = self._middle.closed
        index_hold = self._index.held_for(now)

        self._index.update(features.pinch_index, now, config)
        self._middle.update(features.pinch_middle, now, config)
        self._elect_owner()

        # Releases are evaluated before the new state, because a click is defined by the release.
        #
        # A drag always ends, owner or not: the release blocks below are the only place a held
        # button is let go on this path, and skipping one because the pair lost an election would
        # leave a real mouse button down. Ownership gates *emitting a click*, never releasing.
        if was_index_closed and not self._index.closed:
            if self._dragging:
                self._dragging = False
                self._emit(GestureEventType.DRAG_END)
            elif self._owner == "index" and index_hold < config.hold_to_drag_seconds:
                self._latch("left_click", now)
                self._emit(GestureEventType.LEFT_CLICK)
            self._state = MachineState.IDLE

        # **No deadline on this one.** The index pair is gated on `hold_to_drag_seconds` because
        # overrunning it means something else — a drag. The middle pair has no second mode, so the
        # same gate resolved a long right click into *nothing*: the release fell through a branch
        # that does not exist and the gesture disappeared without a trace. A gate with nothing on
        # the other side does not decide anything, it just discards.
        if was_middle_closed and not self._middle.closed:
            if self._owner == "middle":
                self._latch("right_click", now)
                self._emit(GestureEventType.RIGHT_CLICK)
            self._state = MachineState.IDLE

        # Cleared only now, so the block above could still consult it on the frame the last pair
        # opened — that frame *is* the click.
        if not self._index.closed and not self._middle.closed:
            self._owner = None

        # Leaving the scroll pose ends the current scroll gesture; the next one starts fresh
        # rather than inheriting a stale origin.
        if self._state is not MachineState.SCROLL:
            self._scroll_origin = None

        # The owner decides, not the order the pairs happen to be checked in. A held right click
        # made with a curled index closes this pair too, and without the guard it would start a
        # drag — the same defect as the double click, with a held mouse button instead.
        if self._index.closed and self._owner == "index":
            held = self._index.held_for(now)
            if held >= config.hold_to_drag_seconds:
                if not self._dragging:
                    self._dragging = True
                    self._emit(GestureEventType.DRAG_START)
                self._state = MachineState.DRAG
                return "drag"
            self._state = MachineState.PINCH_INDEX_PENDING
            # Undecided on purpose: it is not yet a click and not yet a drag. Reporting either
            # would be a guess. The pending state is visible in debug output for tuning.
            return self._latched_or_none(now)

        if self._middle.closed and self._owner == "middle":
            self._state = MachineState.PINCH_MIDDLE_PENDING
            return self._latched_or_none(now)

        # A pair may still be closed here without owning the gesture — the thumb is near that
        # fingertip only because it is near the other one. Deliberately no early return: the pose
        # checks below decide what to report, rather than this frame being forced to "idle".

        extended = features.extended
        if extended["index"] and extended["middle"] and not extended["ring"] and not extended["pinky"]:
            self._update_scroll(features)
            self._state = MachineState.SCROLL
            return "scroll"

        self._scroll_origin = None

        if extended["index"]:
            self._state = MachineState.MOVE
            return self._latched_or(now, "move")

        self._state = MachineState.IDLE
        return self._latched_or_none(now)

    def _update_scroll(self, features: HandFeatures) -> None:
        """Emit one scroll step per `scroll_step` of vertical hand travel.

        The origin advances by whole steps rather than resetting to the current position, so a
        slow continuous drag scrolls smoothly instead of stalling just short of the threshold.
        """
        anchor_y = features.anchor[1]
        if self._scroll_origin is None:
            self._scroll_origin = anchor_y
            return

        travel = anchor_y - self._scroll_origin
        steps = int(travel / self.config.scroll_step)
        if steps == 0:
            return

        self._scroll_origin += steps * self.config.scroll_step
        # Screen y grows downward; moving the hand up should scroll the content up, which is a
        # positive wheel step.
        self._emit(GestureEventType.SCROLL, scroll_steps=-steps)

    def _latch(self, gesture: Gesture, now: float) -> None:
        self._latched = gesture
        self._latched_until = now + self.config.click_latch_seconds

    def _latched_or(self, now: float, fallback: Gesture) -> Gesture:
        if self._latched is not None and now < self._latched_until:
            return self._latched
        self._latched = None
        return fallback

    def _latched_or_none(self, now: float) -> Gesture:
        return self._latched_or(now, "none")

    def _debug(self, features: HandFeatures) -> GestureDebug:
        return GestureDebug(
            state=self._state.value,
            hand_scale=features.hand_scale,
            pinch_index=features.pinch_index,
            pinch_middle=features.pinch_middle,
            extended=features.extended,
            angles=features.angles,
            pinch_close=self.config.pinch_close,
            pinch_open=self.config.pinch_open,
        )
