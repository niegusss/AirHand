"""Global keyboard kill-switch.

The one control that must work when nothing else does. If the cursor is being driven badly, the
user cannot click a button to stop it — the pointer is exactly what is broken. A global hotkey is
independent of the cursor, of the UI process, and of whether the window has focus.

Listens whenever the engine is running, not only while actuation is enabled: pressing it when
nothing is armed should be a harmless no-op, not a missed chance to stop something.
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)

DEFAULT_HOTKEY = "<ctrl>+<alt>+<space>"


class KillSwitch:
    """Runs a global hotkey listener on its own thread."""

    def __init__(self, on_trigger: Callable[[], None], *, hotkey: str = DEFAULT_HOTKEY) -> None:
        self._on_trigger = on_trigger
        self._hotkey = hotkey
        self._listener = None

    @property
    def hotkey(self) -> str:
        return self._hotkey

    @property
    def active(self) -> bool:
        return self._listener is not None

    def start(self) -> bool:
        """Begin listening. Returns False if the hotkey could not be registered.

        A failure here is serious but not fatal: the engine still runs, and the caller is expected
        to refuse to arm actuation without a working kill-switch.
        """
        if self._listener is not None:
            return True

        try:
            from pynput import keyboard
        except Exception as exc:  # noqa: BLE001
            log.error("Kill-switch unavailable, pynput could not be imported: %s", exc)
            return False

        try:
            listener = keyboard.GlobalHotKeys({self._hotkey: self._fire})
            listener.daemon = True
            listener.start()
        except Exception as exc:  # noqa: BLE001 - malformed combo, no input access, etc.
            log.error("Kill-switch could not register %s: %s", self._hotkey, exc)
            return False

        self._listener = listener
        log.info("Kill-switch armed: %s disables cursor actuation", self._hotkey)
        return True

    def stop(self) -> None:
        if self._listener is None:
            return
        try:
            self._listener.stop()
        except Exception:  # noqa: BLE001
            log.debug("Kill-switch listener failed to stop cleanly", exc_info=True)
        self._listener = None

    def _fire(self) -> None:
        log.warning("KILL-SWITCH pressed — disabling cursor actuation")
        try:
            self._on_trigger()
        except Exception:  # noqa: BLE001 - the callback must never kill the listener thread,
            # or the kill-switch would work exactly once.
            log.exception("Kill-switch callback failed")
