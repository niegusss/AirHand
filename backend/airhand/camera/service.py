"""Camera Service — device discovery and frame capture.

Owns every OpenCV call in the project. Keeping the platform-specific parts here is what makes the
later macOS/Linux port touch only this module and the Cursor Engine.

Interprets no hand data: it hands raw BGR frames to the Tracking Engine and nothing more.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Iterator

import cv2
import numpy as np

log = logging.getLogger(__name__)

# Probing is the only portable way to enumerate with OpenCV — there is no device-list API.
# Each miss costs a backend open attempt, so keep the ceiling low.
MAX_PROBE_INDEX = 5


@dataclass(frozen=True)
class CameraInfo:
    index: int
    name: str
    width: int
    height: int
    """What the driver actually granted — requests are advisory and often silently ignored."""
    fps: float = 0.0
    fourcc: str = ""


BACKEND_ALIASES: dict[str, int] = {
    "auto": cv2.CAP_ANY,
    "msmf": cv2.CAP_MSMF,
    "dshow": cv2.CAP_DSHOW,
}


def _preferred_backends() -> list[int]:
    """Backends to try, best first.

    MSMF is first on Windows and that order is measured, not assumed: on the dev machine
    DirectShow collapses to ~9 fps at 1280x720 because it will only deliver uncompressed YUY2,
    which exceeds the USB bandwidth, while MSMF holds 30 fps at both 640x480 and 1280x720.
    Reproduce with `tools/bench_camera.py`.

    OpenCV 5 reports "backend generally available but can't be used to capture by index" when no
    device is attached, so a failure here usually means the webcam is unplugged rather than that
    the backend is broken.
    """
    if sys.platform == "win32":
        return [cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY]
    return [cv2.CAP_ANY]


def discover_cameras(max_index: int = MAX_PROBE_INDEX) -> list[CameraInfo]:
    """Probe indices until a gap, returning every device that yields a real frame.

    Device *names* are not available through OpenCV on Windows without an extra dependency
    (DirectShow enumeration), so cameras are reported by index. Worth revisiting if users end up
    with several cameras and cannot tell them apart.
    """
    found: list[CameraInfo] = []

    for index in range(max_index):
        opened = _open_capture(index)
        if opened is None:
            continue
        capture, backend = opened
        try:
            ok, frame = capture.read()
            if ok and frame is not None:
                height, width = frame.shape[:2]
                found.append(
                    CameraInfo(
                        index=index,
                        name=f"Camera {index} ({_backend_name(backend)})",
                        width=int(width),
                        height=int(height),
                    )
                )
        finally:
            capture.release()

    return found


def _decode_fourcc(value: float) -> str:
    """Turn OpenCV's float-encoded FOURCC into its four characters (e.g. 'MJPG', 'YUY2').

    Returns "" when the backend does not report one — MSMF often yields NUL bytes rather than a
    real code, and those are not whitespace, so they must be filtered explicitly or they print as
    invisible garbage.
    """
    code = int(value)
    if code <= 0:
        return ""
    chars = [chr((code >> (8 * shift)) & 0xFF) for shift in range(4)]
    decoded = "".join(char for char in chars if char.isprintable()).strip()
    return decoded


def _backend_name(backend: int) -> str:
    return {cv2.CAP_MSMF: "MSMF", cv2.CAP_DSHOW: "DirectShow", cv2.CAP_ANY: "auto"}.get(
        backend, str(backend)
    )


def _open_capture(index: int, backend: str | None = None) -> tuple[cv2.VideoCapture, int] | None:
    candidates = [BACKEND_ALIASES[backend]] if backend else _preferred_backends()
    for candidate in candidates:
        capture = cv2.VideoCapture(index, candidate)
        if capture.isOpened():
            return capture, candidate
        capture.release()
    return None


class CameraUnavailable(RuntimeError):
    """No camera could be opened at the requested index."""


class CameraService:
    """Opens one camera and yields frames.

    Not thread-safe by itself — the live pipeline owns exactly one instance on one thread.
    """

    def __init__(
        self,
        index: int = 0,
        *,
        # 640x480 by default, chosen from measurement: this webcam delivers 30 fps at both
        # 640x480 and 1280x720, so the higher resolution buys no throughput while costing ~3 ms
        # more inference per frame. MediaPipe's landmark model downscales internally anyway, so
        # the extra pixels mostly do not reach it. Raise it if accuracy testing shows otherwise.
        width: int = 640,
        height: int = 480,
        fps: float = 60.0,
        prefer_mjpg: bool = True,
        backend: str | None = None,
    ) -> None:
        self._index = index
        self._requested_width = width
        self._requested_height = height
        self._requested_fps = fps
        self._prefer_mjpg = prefer_mjpg
        self._backend = backend
        self._capture: cv2.VideoCapture | None = None
        self._info: CameraInfo | None = None

    @property
    def info(self) -> CameraInfo | None:
        return self._info

    def open(self) -> CameraInfo:
        opened = _open_capture(self._index, self._backend)
        if opened is None:
            raise CameraUnavailable(
                f"Could not open camera {self._index}. "
                "Check that a webcam is connected and not in use by another application."
            )

        capture, backend = opened

        # Requesting MJPG is a portability hedge: on many webcams an uncompressed stream is
        # bandwidth-limited over USB and caps the frame rate. Measured on the dev machine
        # (Thronmax Stream Go, 2026-08-08) it is a NO-OP — DirectShow stays on YUY2 whatever the
        # call order, and MSMF does not report a FOURCC at all. Kept because it costs nothing and
        # helps elsewhere, but do not credit it for this machine's frame rate.
        if self._prefer_mjpg:
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._requested_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._requested_height)
        # This one measurably matters: without an explicit request the driver may settle on a
        # slower default profile. Asking for 60 took the dev machine from ~16-20 fps to a solid 30
        # (which is its ceiling). Drivers clamp to what they support, so over-asking is safe.
        capture.set(cv2.CAP_PROP_FPS, self._requested_fps)

        ok, frame = capture.read()
        if not ok or frame is None:
            capture.release()
            raise CameraUnavailable(
                f"Camera {self._index} opened but returned no frames. "
                "Another application may be holding it."
            )

        height, width = frame.shape[:2]
        self._capture = capture
        self._info = CameraInfo(
            index=self._index,
            name=f"Camera {self._index} ({_backend_name(backend)})",
            width=int(width),
            height=int(height),
            fps=float(capture.get(cv2.CAP_PROP_FPS)),
            fourcc=_decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC)),
        )
        # Log what was granted, not what was asked for — the two often differ and only the
        # granted values explain the frame rate you actually get.
        log.info(
            "Opened %s at %dx%d, driver reports %.1f fps, format %s",
            self._info.name,
            width,
            height,
            self._info.fps,
            self._info.fourcc or "unknown",
        )
        return self._info

    def read(self) -> np.ndarray | None:
        """Return the next BGR frame, or None if the device stopped delivering."""
        if self._capture is None:
            return None
        ok, frame = self._capture.read()
        if not ok or frame is None:
            return None
        return frame

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            frame = self.read()
            if frame is None:
                return
            yield frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._info = None

    def __enter__(self) -> "CameraService":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
