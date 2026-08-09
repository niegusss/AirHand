"""Tracking Engine — MediaPipe hand landmark detection.

Detects one hand and estimates its 21 landmarks (FR-001, FR-002). It decides nothing about what a
gesture *means* — that belongs to the Gesture Engine.

The model file is vendored in `models/` so first run needs no download, which is what makes
FR-011 (fully offline) true.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    HandLandmarker,
    HandLandmarkerOptions,
    RunningMode,
)
import mediapipe as mp

# *Which* model this is lives in `airhand/model.py`, outside this module on purpose: the profile
# store stamps it and the CLI reports it, and neither should have to import MediaPipe to ask.
from ..model import MODEL_FILENAME, MODEL_VARIANT, MODEL_VERSION, model_path

log = logging.getLogger(__name__)

__all__ = [
    "MODEL_FILENAME",
    "MODEL_VARIANT",
    "MODEL_VERSION",
    "HandResult",
    "TrackingEngine",
    "model_path",
]


@dataclass(frozen=True)
class HandResult:
    detected: bool
    handedness: str | None
    """21 landmarks as [x, y, z], normalized 0..1 in image space."""
    landmarks: list[list[float]] | None


def handedness_label(category_name: str | None) -> str | None:
    """MediaPipe's hand label, lowercased and otherwise untouched.

    **Never swap Left for Right here.** It was done once, justified by the preview being mirrored
    for the user, and that reasoning is simply wrong: mirroring a picture does not change which
    physical hand is in it. The result was a left hand reported as "right" on the Dashboard.

    The only reason anyone ever swaps this output belongs to the **legacy Solutions API**, which
    documented handedness as assuming a mirrored, selfie-style image. MediaPipe **Tasks** — what
    this project uses — makes no such assumption and labels the hand it sees. That is now the
    second Solutions-era assumption to have reached this file; the model variant was the first.

    Pulled out of :meth:`TrackingEngine.detect` as a plain function purely so it can be tested:
    everything around it needs a live landmarker, which is why the swap survived unexamined.
    """
    if category_name is None:
        return None
    return category_name.lower()


class TrackingEngine:
    """Wraps MediaPipe's HandLandmarker in VIDEO mode.

    VIDEO mode (rather than LIVE_STREAM) keeps detection synchronous and deterministic: one frame
    in, one result out. That makes latency measurable and tests reproducible, which matters more
    here than the small throughput win from the async callback API.
    """

    def __init__(self, *, max_hands: int = 1, min_detection_confidence: float = 0.5) -> None:
        self._max_hands = max_hands
        self._min_detection_confidence = min_detection_confidence
        self._landmarker: HandLandmarker | None = None
        self._last_timestamp_ms = -1

    def open(self) -> None:
        path = model_path()
        if not path.is_file():
            raise FileNotFoundError(
                f"Hand landmark model not found at {path}. "
                "It is vendored in models/ so the app works offline — see backend/README.md."
            )

        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(path)),
            running_mode=RunningMode.VIDEO,
            num_hands=self._max_hands,
            min_hand_detection_confidence=self._min_detection_confidence,
            min_tracking_confidence=self._min_detection_confidence,
        )
        self._landmarker = HandLandmarker.create_from_options(options)
        log.info("Tracking engine ready (model %s %s)", MODEL_VARIANT, MODEL_VERSION)

    def detect(self, frame_rgb: np.ndarray, timestamp_ms: int) -> HandResult:
        """Run detection on one RGB frame.

        `timestamp_ms` must increase strictly between calls — MediaPipe's VIDEO mode rejects
        out-of-order timestamps.
        """
        if self._landmarker is None:
            raise RuntimeError("TrackingEngine.open() must be called first")

        if timestamp_ms <= self._last_timestamp_ms:
            timestamp_ms = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp_ms

        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        result = self._landmarker.detect_for_video(image, timestamp_ms)

        if not result.hand_landmarks:
            return HandResult(detected=False, handedness=None, landmarks=None)

        landmarks = [
            [round(float(point.x), 4), round(float(point.y), 4), round(float(point.z), 4)]
            for point in result.hand_landmarks[0]
        ]

        raw = None
        if result.handedness and result.handedness[0]:
            raw = result.handedness[0][0].category_name

        return HandResult(
            detected=True, handedness=handedness_label(raw), landmarks=landmarks
        )

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None
        self._last_timestamp_ms = -1

    def __enter__(self) -> "TrackingEngine":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
