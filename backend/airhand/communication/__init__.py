"""Communication layer — WebSocket server, status/settings/telemetry messages.

Contains no computer-vision logic.
"""

from .server import EngineServer

__all__ = ["EngineServer"]
