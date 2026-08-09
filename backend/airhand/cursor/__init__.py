"""Cursor Engine — moving the cursor, click events, drag events, scrolling.

The only part of the project that writes to the operating system.
"""

from .backends import BackendUnavailable, DryRunBackend, InputBackend, PynputBackend
from .engine import CursorEngine, CursorState, build_cursor_engine
from .killswitch import DEFAULT_HOTKEY, KillSwitch
from .mapping import ActiveArea, active_area_for, to_screen
from .screen import ScreenSize, ScreenUnavailable, primary_screen

__all__ = [
    "ActiveArea",
    "BackendUnavailable",
    "CursorEngine",
    "CursorState",
    "DEFAULT_HOTKEY",
    "DryRunBackend",
    "InputBackend",
    "KillSwitch",
    "PynputBackend",
    "ScreenSize",
    "ScreenUnavailable",
    "active_area_for",
    "build_cursor_engine",
    "primary_screen",
    "to_screen",
]
