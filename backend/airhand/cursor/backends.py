"""Input backends.

The Cursor Engine talks to one of these rather than to `pynput` directly, for three reasons:
tests must never move the real cursor, a dry-run mode makes it possible to exercise the whole
actuation path safely, and the OS-specific dependency stays at one seam.
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class InputBackend(Protocol):
    """Everything the Cursor Engine can do to the machine."""

    def move(self, x: int, y: int) -> None: ...

    def press_left(self) -> None: ...

    def release_left(self) -> None: ...

    def click_left(self) -> None: ...

    def click_right(self) -> None: ...

    def scroll(self, steps: int) -> None: ...


class BackendUnavailable(RuntimeError):
    """The OS input backend could not be created."""


class PynputBackend:
    """Real OS input. The only class in the project that synthesizes input events."""

    def __init__(self) -> None:
        try:
            from pynput.mouse import Button, Controller
        except Exception as exc:  # noqa: BLE001 - import failure is a runtime condition here
            raise BackendUnavailable(f"pynput is unavailable: {exc}") from exc

        self._button = Button
        self._mouse = Controller()

    def move(self, x: int, y: int) -> None:
        self._mouse.position = (x, y)

    def press_left(self) -> None:
        self._mouse.press(self._button.left)

    def release_left(self) -> None:
        self._mouse.release(self._button.left)

    def click_left(self) -> None:
        self._mouse.click(self._button.left)

    def click_right(self) -> None:
        self._mouse.click(self._button.right)

    def scroll(self, steps: int) -> None:
        self._mouse.scroll(0, steps)


class DryRunBackend:
    """Logs what would have happened without touching the machine.

    Exists so the full actuation path — mapping, event handling, drag bookkeeping — can be
    exercised against a real hand without the risk of a stray click. Select with
    `--cursor-dry-run`.
    """

    def __init__(self) -> None:
        self._last_position: tuple[int, int] | None = None

    def move(self, x: int, y: int) -> None:
        # Movement happens every frame; log only meaningful jumps or this drowns the log.
        if self._last_position is None or max(
            abs(x - self._last_position[0]), abs(y - self._last_position[1])
        ) >= 40:
            log.info("[dry-run] move -> (%d, %d)", x, y)
            self._last_position = (x, y)

    def press_left(self) -> None:
        log.info("[dry-run] press left")

    def release_left(self) -> None:
        log.info("[dry-run] release left")

    def click_left(self) -> None:
        log.info("[dry-run] click left")

    def click_right(self) -> None:
        log.info("[dry-run] click right")

    def scroll(self, steps: int) -> None:
        log.info("[dry-run] scroll %+d", steps)
