"""Camera Service — device discovery, frame capture, frame streaming.

Interprets no hand data.
"""

from .service import CameraInfo, CameraService, CameraUnavailable, discover_cameras

__all__ = ["CameraInfo", "CameraService", "CameraUnavailable", "discover_cameras"]
