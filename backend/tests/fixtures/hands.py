"""Hand fixtures for gesture tests.

Deliberately a thin re-export of the production geometry in `airhand.handmodel`, which the
synthetic telemetry source also uses. A separate fixture copy would drift from the demo data and
then test something nobody runs.
"""

from airhand.handmodel import (
    FINGERS,
    FIST,
    OPEN_HAND,
    POINTING,
    SCROLL_POSE,
    HandPose,
    make_hand,
)

__all__ = [
    "FINGERS",
    "FIST",
    "OPEN_HAND",
    "POINTING",
    "SCROLL_POSE",
    "HandPose",
    "make_hand",
]
