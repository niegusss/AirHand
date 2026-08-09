"""Hand-space conversion and feature extraction.

This module is where "must work on any webcam" is actually implemented. Two corrections happen
here, once, so nothing downstream has to think about the camera again:

**Aspect ratio.** MediaPipe normalises `x` by frame width and `y` by frame height. On a 16:9
camera one horizontal unit is therefore physically wider than one vertical unit, so a Euclidean
distance computed on raw normalised coordinates is anisotropic — the same pinch measures
differently on 4:3 and 16:9 hardware. Multiplying `x` by the aspect ratio puts both axes in units
of frame *height*, making distances and angles comparable across cameras.

**Hand scale.** Even in isotropic units, a hand held closer to the lens is bigger, and a wide-angle
lens shrinks everything. Absolute distances are therefore meaningless as thresholds. Every distance
is divided by a rigid reference length — wrist to middle-finger MCP — which is a palm bone and so
does not change when fingers flex. Distances become multiples of the user's own palm, which is what
makes one threshold work for every camera, every lens and every distance from it.

`z` is deliberately ignored. MediaPipe's depth is relative, poorly scaled and noisy; using it would
reintroduce the camera dependence this module exists to remove.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

# MediaPipe hand landmark indices.
WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_TIP = 8
MIDDLE_MCP = 9
MIDDLE_TIP = 12
RING_MCP = 13
PINKY_MCP = 17

# The rigid part of the hand: wrist plus the four knuckles. None of them move when fingers flex,
# so their centroid describes where the hand *is* without describing what it is doing.
PALM_LANDMARKS = (WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP)

Point = tuple[float, float]

# (mcp, pip, tip) per finger. The angle at the middle joint is the curl signal.
FINGER_JOINTS: dict[str, tuple[int, int, int]] = {
    "thumb": (1, 2, 4),
    "index": (5, 6, 8),
    "middle": (9, 10, 12),
    "ring": (13, 14, 16),
    "pinky": (17, 18, 20),
}

FINGER_NAMES = tuple(FINGER_JOINTS)

# Below this, a hand is too small or too degenerate to derive stable ratios from — usually a
# partially visible hand at the frame edge. Better to report nothing than to divide by noise.
MIN_HAND_SCALE = 1e-4


@dataclass(frozen=True)
class HandFeatures:
    """Camera-independent description of one hand pose."""

    hand_scale: float
    """Thumb-tip to index-tip distance, in multiples of hand scale."""
    pinch_index: float
    """Thumb-tip to middle-tip distance, in multiples of hand scale."""
    pinch_middle: float
    extended: dict[str, bool]
    """Curl angle at each finger's middle joint, in degrees. 180 is straight."""
    angles: dict[str, float]
    """Palm centroid in hand-space units, divided by hand scale.

    The palm rather than a fingertip, because it does not move when the hand pinches — the same
    reason it anchors the cursor. Divided by hand scale so that a vertical delta means "moved half
    a palm", independent of distance from the lens.
    """
    anchor: tuple[float, float]


def to_hand_space(landmarks: Sequence[Sequence[float]], aspect: float) -> list[Point]:
    """Convert normalised landmarks to isotropic units of frame height.

    `aspect` is frame width / height.
    """
    return [(float(point[0]) * aspect, float(point[1])) for point in landmarks]


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hand_scale(points: Sequence[Point]) -> float:
    """Wrist to middle-finger MCP — a palm bone, so it is invariant to finger flexion.

    Deliberately not the span of the open hand or the wrist-to-fingertip distance: both change
    dramatically as the hand opens and closes, which would make every ratio move while the user is
    performing the very gesture we are trying to measure.
    """
    if len(points) <= MIDDLE_MCP:
        return 0.0
    return distance(points[WRIST], points[MIDDLE_MCP])


def palm_center(points: Sequence[Sequence[float]]) -> Point:
    """Centroid of the five rigid palm landmarks.

    Used instead of a single landmark wherever "where is the hand" is the question — the cursor
    anchor and the scroll reference. Averaging five points attenuates the part of MediaPipe's
    per-landmark noise that is independent between landmarks, and it costs nothing in latency
    because it is stateless: no filter, no history, no lag.

    Accepts raw normalized landmarks (``[x, y, z]``) as readily as hand-space points, so the
    pointer stage and the feature extractor can share it without converting first.
    """
    total_x = 0.0
    total_y = 0.0
    for index in PALM_LANDMARKS:
        total_x += float(points[index][0])
        total_y += float(points[index][1])
    count = len(PALM_LANDMARKS)
    return (total_x / count, total_y / count)


def joint_angle(points: Sequence[Point], mcp: int, pip: int, tip: int) -> float:
    """Angle at `pip` between the bones to `mcp` and to `tip`, in degrees.

    Angles are scale-invariant by construction, so this needs no division by hand scale.
    """
    a = (points[mcp][0] - points[pip][0], points[mcp][1] - points[pip][1])
    b = (points[tip][0] - points[pip][0], points[tip][1] - points[pip][1])

    length_a = math.hypot(*a)
    length_b = math.hypot(*b)
    if length_a == 0.0 or length_b == 0.0:
        return 0.0

    cosine = (a[0] * b[0] + a[1] * b[1]) / (length_a * length_b)
    # Guard against floating point pushing the value just outside acos's domain.
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def extract(
    landmarks: Sequence[Sequence[float]],
    *,
    aspect: float,
    extended_angle_degrees: float,
) -> HandFeatures | None:
    """Turn raw landmarks into camera-independent features, or None if the hand is unusable."""
    if len(landmarks) < 21:
        return None

    points = to_hand_space(landmarks, aspect)
    scale = hand_scale(points)
    if scale < MIN_HAND_SCALE:
        return None

    palm = palm_center(points)

    angles: dict[str, float] = {}
    extended: dict[str, bool] = {}
    for name, (mcp, pip, tip) in FINGER_JOINTS.items():
        angle = joint_angle(points, mcp, pip, tip)
        angles[name] = angle
        extended[name] = angle >= extended_angle_degrees

    return HandFeatures(
        hand_scale=scale,
        pinch_index=distance(points[THUMB_TIP], points[INDEX_TIP]) / scale,
        pinch_middle=distance(points[THUMB_TIP], points[MIDDLE_TIP]) / scale,
        extended=extended,
        angles=angles,
        anchor=(palm[0] / scale, palm[1] / scale),
    )
