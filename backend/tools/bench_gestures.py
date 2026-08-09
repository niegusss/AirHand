r"""Motion Filter + Gesture Engine cost per frame.

Answers one question: how much of the frame budget do smoothing and classification consume?

Worth having separately from the engine log, because `inferenceMs` there times only MediaPipe.
Filtering and classification sit outside that timer, so a rise in `inferenceMs` can never be
blamed on them — this script is how you check what they actually cost.

    cd backend
    .\.venv\Scripts\python.exe tools\bench_gestures.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Run from anywhere: tools/ is not a package and only its own directory lands on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from airhand.filters import LandmarkFilter  # noqa: E402
from airhand.gestures import GestureConfig, GestureEngine  # noqa: E402
from airhand.handmodel import POINTING, SCROLL_POSE, make_hand  # noqa: E402

FRAMES = 20_000
DT = 1 / 30


def bench(label: str, landmarks_for) -> None:
    landmark_filter = LandmarkFilter(count=21)
    engine = GestureEngine(config=GestureConfig())

    # Warm up so the first-sample paths and any lazy allocation are out of the measurement.
    for index in range(200):
        engine.update(landmark_filter.filter(landmarks_for(index), DT), aspect=4 / 3, now=index * DT)

    started = time.perf_counter()
    for index in range(FRAMES):
        smoothed = landmark_filter.filter(landmarks_for(index), DT)
        engine.update(smoothed, aspect=4 / 3, now=index * DT)
    elapsed = time.perf_counter() - started

    per_frame_us = elapsed / FRAMES * 1e6
    budget_share = (per_frame_us / 1e6) / (1 / 30) * 100
    print(f"{label:28} {per_frame_us:7.1f} us/frame   {budget_share:5.2f}% of a 30 fps budget")


def _moving(index: int):
    # A drifting hand so the filter's adaptive path is exercised, not just its steady state.
    offset = (index % 100) * 0.002
    return make_hand(POINTING, center=(0.4 + offset, 0.6), pinch_index=0.9)


def _pinching(index: int):
    gap = 0.15 if (index // 15) % 2 == 0 else 0.9
    return make_hand(POINTING, center=(0.5, 0.6), pinch_index=gap)


def _scrolling(index: int):
    offset = (index % 60) * 0.001
    return make_hand(SCROLL_POSE, center=(0.5, 0.6 + offset), pinch_index=0.9)


print(f"{FRAMES} frames per case\n")
bench("moving (classified move)", _moving)
bench("pinch cycling (click/drag)", _pinching)
bench("scroll pose", _scrolling)
