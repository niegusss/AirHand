"""Camera throughput benchmark — raw capture only, no MediaPipe involved.

Answers one question: what frame rate can this webcam actually deliver, and under which
combination of backend, pixel format and resolution?

Run it whenever the camera or machine changes — the defaults in `camera/service.py` were chosen
from its output, not from assumption. Needed again to settle the reference-hardware baseline.

    cd backend
    .\.venv\Scripts\python.exe tools\bench_camera.py

Results on the dev machine (Thronmax Stream Go, 2026-08-08):

    backend  res        format   claims  actual
    DSHOW    640x480    YUY2       60.0    29.9
    DSHOW    1280x720   YUY2       60.0     8.9   <- uncompressed, over USB bandwidth
    MSMF     640x480    unknown    30.0    29.9
    MSMF     1280x720   unknown    30.0    30.1

Conclusions: 30 fps is this device's ceiling; MSMF beats DirectShow decisively at 720p; the MJPG
request is a no-op on both backends here.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2

# Run from anywhere: tools/ is not a package and only its own directory lands on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from airhand.camera.service import _decode_fourcc  # noqa: E402

BACKENDS = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)]
RESOLUTIONS = [(640, 480), (1280, 720)]
FOURCC_ORDER = ["fourcc-first", "fourcc-after-size", "no-mjpg"]

WARMUP_FRAMES = 10
MEASURE_SECONDS = 3.0


def measure(backend_id: int, width: int, height: int, order: str) -> tuple[float, str, float]:
    capture = cv2.VideoCapture(0, backend_id)
    if not capture.isOpened():
        capture.release()
        return 0.0, "n/a", 0.0

    mjpg = cv2.VideoWriter_fourcc(*"MJPG")
    try:
        if order == "fourcc-first":
            capture.set(cv2.CAP_PROP_FOURCC, mjpg)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        elif order == "fourcc-after-size":
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            capture.set(cv2.CAP_PROP_FOURCC, mjpg)
        else:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_FPS, 60)

        for _ in range(WARMUP_FRAMES):
            capture.read()

        fourcc = _decode_fourcc(capture.get(cv2.CAP_PROP_FOURCC)) or "unknown"
        claimed = float(capture.get(cv2.CAP_PROP_FPS))

        frames = 0
        started = time.monotonic()
        while time.monotonic() - started < MEASURE_SECONDS:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            frames += 1
        elapsed = time.monotonic() - started
        return (frames / elapsed if elapsed > 0 else 0.0), fourcc, claimed
    finally:
        capture.release()


print(f"{'backend':8} {'res':10} {'order':18} {'format':8} {'claims':>7} {'actual':>8}")
print("-" * 64)
for name, backend_id in BACKENDS:
    for width, height in RESOLUTIONS:
        for order in FOURCC_ORDER:
            actual, fourcc, claimed = measure(backend_id, width, height, order)
            print(
                f"{name:8} {f'{width}x{height}':10} {order:18} {fourcc:8} "
                f"{claimed:7.1f} {actual:8.1f}"
            )
