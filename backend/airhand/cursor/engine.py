"""Cursor Engine — turns gesture events into OS input.

This is the only part of the project that writes to the machine, so it is written defensively.
The governing constraint: **you cannot click a "stop" button with a cursor that is misbehaving.**
Everything here follows from that.

- Actuation is off until explicitly enabled, every session.
- Any loss of confidence — no hand, no anchor, disabled, shutdown — releases a held button.
  A drag left pressed would hand the user a machine with the left button stuck down.
- Coordinates are clamped before they reach the OS.
- Every synthesized click is logged, so "did it click?" is answerable after the fact.

It consumes `GestureUpdate.events`, never the sampled `gesture` field: that field latches clicks
for ~200 ms so a 10 Hz UI can see them, and acting on a latch would fire one click per frame.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from ..gestures import GestureEventType, GestureUpdate
from .backends import BackendUnavailable, DryRunBackend, InputBackend, PynputBackend
from .mapping import ActiveArea, active_area_for, to_screen
from .screen import ScreenSize, ScreenUnavailable, primary_screen

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CursorState:
    """What the UI needs to render the actuation control honestly."""

    available: bool
    enabled: bool
    reason: str | None = None
    screen_width: int = 0
    screen_height: int = 0
    dry_run: bool = False


class CursorEngine:
    """Maps hand position to the cursor and gesture events to clicks.

    Thread-safe: `handle` runs on the pipeline thread while `set_enabled` can be called from the
    server thread or from the kill-switch listener.
    """

    def __init__(
        self,
        *,
        backend: InputBackend,
        screen: ScreenSize,
        coverage: float = 0.7,
        center: tuple[float, float] = (0.5, 0.5),
        mirror: bool = True,
        dry_run: bool = False,
    ) -> None:
        self._backend = backend
        self._screen = screen
        self._coverage = coverage
        self._center = center
        self._mirror = mirror
        self._dry_run = dry_run

        self._lock = threading.RLock()
        self._enabled = False
        self._dragging = False
        self._area: ActiveArea | None = None
        self._frame_aspect: float | None = None

    # ------------------------------------------------------------------ state

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def state(self) -> CursorState:
        with self._lock:
            return CursorState(
                available=True,
                enabled=self._enabled,
                screen_width=self._screen.width,
                screen_height=self._screen.height,
                dry_run=self._dry_run,
            )

    def set_enabled(self, enabled: bool) -> CursorState:
        with self._lock:
            if enabled == self._enabled:
                return self.state()
            self._enabled = enabled
            if not enabled:
                self._release_locked("actuation disabled")
            log.warning("Cursor actuation %s", "ENABLED" if enabled else "disabled")
            return self.state()

    def set_mapping(
        self, *, coverage: float, center_x: float = 0.5, center_y: float = 0.5
    ) -> None:
        """Change the active area while running, for the Calibration screen.

        The only entry point this module accepts from outside, and deliberately still just one:
        `cursor/` is the only code that writes to the OS, and it stays as small as its safety rules
        allow. This is pure geometry — it cannot arm actuation, cannot press a button and cannot
        release one.

        Validated before assignment, not after: a bad value must leave the current area in force
        rather than raise past a half-applied change. That is why both fields are checked up front
        even though only one of them may have moved.
        """
        if not 0.0 < coverage <= 1.0:
            raise ValueError(f"coverage must be in (0, 1], got {coverage}")
        for name, value in (("center_x", center_x), ("center_y", center_y)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")

        with self._lock:
            if coverage == self._coverage and (center_x, center_y) == self._center:
                return
            self._coverage = coverage
            self._center = (center_x, center_y)
            # Drop the cached area — it was computed from the old values and is keyed only on
            # frame aspect, so nothing else would invalidate it.
            self._area = None

    def active_area(self, frame_aspect: float) -> ActiveArea:
        """Active area for this frame shape, computed once per aspect change."""
        with self._lock:
            if self._area is None or self._frame_aspect != frame_aspect:
                self._frame_aspect = frame_aspect
                self._area = active_area_for(
                    screen_aspect=self._screen.aspect,
                    frame_aspect=frame_aspect,
                    coverage=self._coverage,
                    center=self._center,
                )
                log.info(
                    "Active area %.0f%% x %.0f%% of frame -> %dx%d screen",
                    self._area.width * 100,
                    self._area.height * 100,
                    self._screen.width,
                    self._screen.height,
                )
            return self._area

    # ----------------------------------------------------------------- action

    def handle(
        self,
        update: GestureUpdate,
        *,
        anchor: tuple[float, float] | None,
        frame_aspect: float,
    ) -> None:
        """Apply one frame's worth of gesture output.

        `anchor` is the cursor anchor in normalized frame coordinates — the index knuckle, not the
        fingertip, because the fingertip travels toward the thumb during a pinch and would drag
        the cursor at the exact moment of a click.
        """
        with self._lock:
            if not self._enabled:
                return

            # A drag must not survive the hand disappearing.
            if anchor is None:
                self._release_locked("hand lost")
                return

            area = self.active_area(frame_aspect)
            x, y = to_screen(
                anchor[0],
                anchor[1],
                area=area,
                screen_width=self._screen.width,
                screen_height=self._screen.height,
                mirror=self._mirror,
            )
            self._backend.move(x, y)

            for event in update.events:
                self._apply_locked(event.type, event.scroll_steps, at=(x, y))

    def _apply_locked(
        self, event_type: GestureEventType, scroll_steps: int, *, at: tuple[int, int]
    ) -> None:
        if event_type is GestureEventType.LEFT_CLICK:
            log.info("click left at (%d, %d)", *at)
            self._backend.click_left()
        elif event_type is GestureEventType.RIGHT_CLICK:
            log.info("click right at (%d, %d)", *at)
            self._backend.click_right()
        elif event_type is GestureEventType.DRAG_START:
            if not self._dragging:
                log.info("drag start at (%d, %d)", *at)
                self._dragging = True
                self._backend.press_left()
        elif event_type is GestureEventType.DRAG_END:
            self._release_locked("drag ended")
        elif event_type is GestureEventType.SCROLL and scroll_steps:
            self._backend.scroll(scroll_steps)

    def release_all(self, reason: str = "shutdown") -> None:
        """Drop any held button. Safe to call repeatedly and from any thread."""
        with self._lock:
            self._release_locked(reason)

    def _release_locked(self, reason: str) -> None:
        if not self._dragging:
            return
        self._dragging = False
        log.info("releasing left button (%s)", reason)
        try:
            self._backend.release_left()
        except Exception:  # noqa: BLE001 - a stuck button is worse than a noisy log
            log.exception("Failed to release the left button — it may still be held")


def build_cursor_engine(
    *,
    coverage: float = 0.7,
    center: tuple[float, float] = (0.5, 0.5),
    mirror: bool = True,
    dry_run: bool = False,
) -> tuple[CursorEngine | None, CursorState]:
    """Create a Cursor Engine, or explain why actuation is unavailable.

    Never raises: a machine without a usable input backend or screen must still run the rest of
    the pipeline, with the UI honestly reporting that cursor control cannot be offered.
    """
    try:
        screen = primary_screen()
    except ScreenUnavailable as exc:
        log.warning("Cursor actuation unavailable: %s", exc)
        return None, CursorState(available=False, enabled=False, reason=str(exc))

    try:
        backend: InputBackend = DryRunBackend() if dry_run else PynputBackend()
    except BackendUnavailable as exc:
        log.warning("Cursor actuation unavailable: %s", exc)
        return None, CursorState(available=False, enabled=False, reason=str(exc))

    engine = CursorEngine(
        backend=backend,
        screen=screen,
        coverage=coverage,
        center=center,
        mirror=mirror,
        dry_run=dry_run,
    )
    log.info(
        "Cursor actuation available: %dx%d%s",
        screen.width,
        screen.height,
        " (dry run)" if dry_run else "",
    )
    return engine, engine.state()
