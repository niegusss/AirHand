"""Motion Filter — exponential smoothing, jitter reduction, interpolation.

Classifies nothing; it only makes the landmark stream stable enough for the Gesture Engine and
the UI overlay to work with.
"""

from .one_euro import LandmarkFilter, OneEuroConfig, OneEuroFilter, Vec2Filter

__all__ = ["LandmarkFilter", "OneEuroConfig", "OneEuroFilter", "Vec2Filter"]
