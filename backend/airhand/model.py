"""Identity of the hand landmark model.

Separate from `tracking/engine.py`, which *uses* the model and therefore imports MediaPipe. Two
things need to know only *which* model this is — the calibration profile, which stamps it, and the
CLI, which reports it — and neither should drag the CV stack in. That split is what keeps
`--source synthetic` runnable on a machine with no MediaPipe installed.

There is no variant to choose. MediaPipe Tasks publishes exactly one Hand Landmarker bundle and
documents it as "HandLandmarker (full)"; the lite/full split belonged to the legacy
`mp.solutions.hands` API and its `model_complexity` flag, which Tasks replaced. This repo labelled
it "lite" until 2026-08-08, which was simply wrong.

Pinned so a swap is deliberate: changing the variant or version shifts landmark placement, and
every gesture threshold is expressed in terms of that placement. Saved calibration profiles carry
these strings and are refused when they no longer match.
"""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_FILENAME = "hand_landmarker.task"
MODEL_VARIANT = "full"
MODEL_VERSION = "float16/1"


def model_path() -> Path:
    """Locate the vendored model in both development and PyInstaller-frozen layouts."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / MODEL_FILENAME
    return Path(__file__).resolve().parents[2] / "models" / MODEL_FILENAME
