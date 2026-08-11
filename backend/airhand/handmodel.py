"""Synthetic hand geometry.

Builds 21-landmark hands with controllable pose, size, position and frame aspect ratio. Used by
the synthetic telemetry source and by the gesture tests, so both exercise the same geometry —
a fixture that drifted from the demo data would test nothing useful.

Geometry is authored in **isotropic** hand space (what `gestures.features.to_hand_space` produces)
and converted back to MediaPipe's normalized space by dividing x by the aspect ratio. Feeding the
result through the real feature extractor with the same aspect therefore recovers exactly the
geometry authored here — which is what lets a caller vary the aspect and expect identical output.

Hand scale is 1.0 in the authored geometry (wrist to middle MCP), so `scale` is literally the
apparent hand size and every distance below reads as a multiple of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]

# Knuckle positions relative to the wrist, in hand-scale units. Middle MCP sits at exactly
# (0, -1) so that |wrist -> middle MCP| == 1.
_MCP: dict[str, Point] = {
    "thumb": (-0.30, -0.25),
    "index": (-0.35, -0.90),
    "middle": (0.00, -1.00),
    "ring": (0.30, -0.95),
    "pinky": (0.58, -0.85),
}

# Direction each finger points when extended.
_DIRECTION: dict[str, Point] = {
    "thumb": (-0.72, -0.70),
    "index": (-0.12, -1.00),
    "middle": (0.00, -1.00),
    "ring": (0.12, -1.00),
    "pinky": (0.26, -0.97),
}

_LENGTH: dict[str, float] = {
    "thumb": 0.62,
    "index": 0.85,
    "middle": 0.92,
    "ring": 0.85,
    "pinky": 0.68,
}

# Landmark indices of (mcp, pip, dip, tip) per finger, matching MediaPipe's layout.
_CHAIN: dict[str, tuple[int, int, int, int]] = {
    "thumb": (1, 2, 3, 4),
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}

FINGERS = tuple(_CHAIN)


@dataclass(frozen=True)
class HandPose:
    """Which fingers are straight. Everything else is derived."""

    thumb: bool = True
    index: bool = True
    middle: bool = True
    ring: bool = True
    pinky: bool = True

    def extended(self, finger: str) -> bool:
        return bool(getattr(self, finger))


OPEN_HAND = HandPose()
FIST = HandPose(thumb=False, index=False, middle=False, ring=False, pinky=False)
POINTING = HandPose(thumb=False, index=True, middle=False, ring=False, pinky=False)
SCROLL_POSE = HandPose(thumb=False, index=True, middle=True, ring=False, pinky=False)


def _finger_points(finger: str, extended: bool) -> list[Point]:
    """MCP, PIP, DIP, TIP for one finger in hand-scale units."""
    mcp = _MCP[finger]
    dx, dy = _DIRECTION[finger]
    length = _LENGTH[finger]

    pip = (mcp[0] + dx * length * 0.42, mcp[1] + dy * length * 0.42)
    if extended:
        # Collinear, so the angle at the PIP is 180 degrees — unambiguously straight.
        dip = (mcp[0] + dx * length * 0.72, mcp[1] + dy * length * 0.72)
        tip = (mcp[0] + dx * length, mcp[1] + dy * length)
        return [mcp, pip, dip, tip]

    # Curled: the segment beyond the PIP folds back toward the palm and slightly across it, which
    # puts the angle at the PIP well under any sane "extended" threshold.
    #
    # The sideways part is a fraction of the finger's own length rather than a fixed offset. As a
    # constant it moved a short pinky as far as a long middle finger, which drew curled fingertips
    # out past the edge of the palm and across their neighbours' knuckles — every curled pose came
    # out as a knot of crossing bones.
    back = (-dx, -dy)
    across = (-dy * 0.16 * length, dx * 0.16 * length)
    dip = (
        pip[0] + back[0] * length * 0.26 + across[0],
        pip[1] + back[1] * length * 0.26 + across[1],
    )
    tip = (
        pip[0] + back[0] * length * 0.46 + across[0],
        pip[1] + back[1] * length * 0.46 + across[1],
    )
    return [mcp, pip, dip, tip]


# A thumb that reaches for a fingertip may stretch, but it may never fold back through its own
# knuckle. Only reachable when the requested gap exceeds the distance to the fingertip — a fist
# asked to hold its thumb wide open, which the demo script does and never draws.
_MIN_THUMB_REACH = 0.10


def _thumb_reaching(target: Point, gap: float) -> list[Point]:
    """Thumb chain that reaches toward `target` and stops `gap` hand-scale units short of it.

    Everything sits on the line from the thumb knuckle to the fingertip, which is the direction a
    thumb actually travels when it reaches for one. The previous version moved the tip alone, along
    a fixed diagonal that ignored where the rest of the thumb was: the measured pinch distance came
    out right, and the drawn hand had a thumb tip flung across the frame with a bone stretched to
    meet it. The overlay is how this fixture is read during UI work, so a pose that measures
    correctly and looks impossible is only half a fixture.

    `gap` is preserved exactly, because it is the quantity the Gesture Engine thresholds on.
    """
    mcp = _MCP["thumb"]
    span = math.hypot(target[0] - mcp[0], target[1] - mcp[1])
    toward = ((target[0] - mcp[0]) / span, (target[1] - mcp[1]) / span)
    reach = max(span - gap, _MIN_THUMB_REACH)
    return [
        mcp,
        (mcp[0] + toward[0] * reach * 0.42, mcp[1] + toward[1] * reach * 0.42),
        (mcp[0] + toward[0] * reach * 0.72, mcp[1] + toward[1] * reach * 0.72),
        (mcp[0] + toward[0] * reach, mcp[1] + toward[1] * reach),
    ]


def make_hand(
    pose: HandPose = OPEN_HAND,
    *,
    scale: float = 0.25,
    center: Point = (0.5, 0.65),
    aspect: float = 1.0,
    pinch_index: float | None = None,
    pinch_middle: float | None = None,
) -> list[list[float]]:
    """Build normalized landmarks for one hand.

    `scale` is the wrist-to-middle-MCP length in normalized units — i.e. apparent hand size.
    `aspect` is frame width / height; output is pre-divided so the feature extractor's correction
    reproduces the authored geometry.
    `pinch_index` / `pinch_middle` place the thumb tip that many hand-scale units from the given
    fingertip — the exact quantity the Gesture Engine thresholds on.
    """
    points: list[Point | None] = [None] * 21
    points[0] = (0.0, 0.0)  # wrist

    for finger in FINGERS:
        chain = _CHAIN[finger]
        for landmark_index, point in zip(chain, _finger_points(finger, pose.extended(finger))):
            points[landmark_index] = point

    reach_for = 8 if pinch_index is not None else 12 if pinch_middle is not None else None
    if reach_for is not None:
        gap = pinch_index if pinch_index is not None else pinch_middle
        assert gap is not None
        target = points[reach_for]
        assert target is not None
        # The whole thumb moves, not just its tip: the pose placed a thumb that is not reaching for
        # anything, and only the tip of it is being asked to.
        for landmark_index, point in zip(_CHAIN["thumb"], _thumb_reaching(target, gap)):
            points[landmark_index] = point

    landmarks: list[list[float]] = []
    for point in points:
        assert point is not None, "every landmark must be assigned"
        x = center[0] + point[0] * scale
        y = center[1] + point[1] * scale
        # Undo the aspect correction the feature extractor will apply.
        landmarks.append([round(x / aspect, 4), round(y, 4), 0.0])
    return landmarks
