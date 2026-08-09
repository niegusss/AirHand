"""Screen geometry.

Platform-specific code lives here and in `camera/`, and nowhere else — that boundary is what keeps
the later macOS/Linux port small.

Primary monitor only. Multi-monitor support is v1.1 in the roadmap, and guessing at a virtual
desktop layout now would produce a cursor that lands on the wrong screen.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScreenSize:
    width: int
    height: int

    @property
    def aspect(self) -> float:
        return self.width / self.height


class ScreenUnavailable(RuntimeError):
    """Screen geometry could not be determined, so absolute mapping is impossible."""


def primary_screen() -> ScreenSize:
    """Size of the primary monitor in physical pixels.

    Physical, not logical: `pynput` positions the cursor in physical pixels, so a DPI-scaled
    logical size would put the cursor at a fraction of the intended position on a scaled display.
    """
    if sys.platform == "win32":
        return _windows_screen()
    raise ScreenUnavailable(
        f"Screen geometry is not implemented for {sys.platform}. "
        "Windows is the MVP target; see memory-bank/projectbrief.md."
    )


def _windows_screen() -> ScreenSize:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    # Without this the process is treated as DPI-unaware and GetSystemMetrics returns scaled
    # (logical) pixels, which would not match the coordinate space pynput moves the cursor in.
    try:
        user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - already-set or unsupported is not fatal
        log.debug("SetProcessDPIAware failed or was already applied", exc_info=True)

    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))

    if width <= 0 or height <= 0:
        raise ScreenUnavailable(f"Windows reported an unusable screen size: {width}x{height}")

    return ScreenSize(width=width, height=height)
