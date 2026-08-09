"""Gesture Engine — classifies Move / Left Click / Right Click / Drag / Scroll.

Emits no OS events; actuation belongs to the Cursor Engine.
"""

from .engine import (
    GestureConfig,
    GestureDebug,
    GestureEngine,
    GestureEvent,
    GestureEventType,
    GestureUpdate,
    MachineState,
)
from .features import HandFeatures, extract, hand_scale, palm_center, to_hand_space

__all__ = [
    "GestureConfig",
    "GestureDebug",
    "GestureEngine",
    "GestureEvent",
    "GestureEventType",
    "GestureUpdate",
    "HandFeatures",
    "MachineState",
    "extract",
    "hand_scale",
    "palm_center",
    "to_hand_space",
]
