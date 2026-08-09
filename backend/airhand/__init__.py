"""AirHand Mouse computer-vision engine.

Owns camera capture, hand tracking, gesture recognition, motion filtering and OS cursor control.
Runs standalone; the desktop UI is a client, not the owner.
"""

from .protocol import ENGINE_VERSION

__version__ = ENGINE_VERSION
__all__ = ["ENGINE_VERSION", "__version__"]
