"""Tracking Engine — hand detection and 21-landmark estimation.

Decides nothing about what a gesture means.
"""

from .engine import (
    MODEL_VARIANT,
    MODEL_VERSION,
    HandResult,
    TrackingEngine,
    model_path,
)

__all__ = [
    "HandResult",
    "MODEL_VARIANT",
    "MODEL_VERSION",
    "TrackingEngine",
    "model_path",
]
