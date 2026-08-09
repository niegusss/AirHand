r"""Cursor jitter and lag, in screen pixels.

Answers the only question that matters for pointer tuning: **how far does the cursor wander when
the hand is holding still, and how far behind does it fall when the hand moves?** Both in pixels,
because that is the unit the user actually experiences — normalized units hide the fact that the
mapping multiplies them by a couple of thousand.

Works in two steps, deliberately. Recording needs a camera and a hand; replaying does not. So one
short session produces a trace that can then be run through as many candidate configurations as
you like, instantly and repeatably. Tuning by "change a number, restart the engine, wave at the
webcam, form an impression" is how you end up with settings nobody can defend.

    cd backend

    # 1. Record ~20 s: hold still, then sweep. Follow the prompts.
    .\.venv\Scripts\python.exe tools\bench_pointer.py --record traces\hand.jsonl

    # 2. Compare configurations against that trace, as often as you like.
    .\.venv\Scripts\python.exe tools\bench_pointer.py --replay traces\hand.jsonl

    # No camera to hand? A synthetic trace exercises the same maths.
    .\.venv\Scripts\python.exe tools\bench_pointer.py --synthetic traces\fake.jsonl

The trace stores **raw** landmarks, so replaying is a true A/B: every candidate sees exactly the
same detector output, on exactly the same frame timings.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

# Run from anywhere: tools/ is not a package and only its own directory lands on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tracefile  # noqa: E402 - sibling module; tools/ is on sys.path for scripts run from it
from airhand.cursor import ScreenSize, active_area_for, to_screen  # noqa: E402
from airhand.cursor.screen import ScreenUnavailable, primary_screen  # noqa: E402
from airhand.gestures.features import INDEX_MCP, PALM_LANDMARKS, palm_center  # noqa: E402
from airhand.pointer import LANDMARKS_REQUIRED, PointerConfig, PointerTracker  # noqa: E402

TRACE_KIND = tracefile.TRACE_KIND
TRACE_VERSION = tracefile.TRACE_VERSION

REST = "rest"
SWEEP = "sweep"

FALLBACK_SCREEN = ScreenSize(1920, 1080)


# --------------------------------------------------------------------------- recording


def record(path: Path, *, seconds: float, width: int, height: int, camera_index: int) -> int:
    """Two labelled halves: hold still, then sweep. The camera loop itself lives in `tracefile`."""
    half = seconds / 2
    announced: set[str] = set()

    def label(elapsed: float) -> dict[str, str]:
        segment = REST if elapsed < half else SWEEP
        if segment not in announced:
            announced.add(segment)
            print(f"  {segment.upper().replace('REST', 'HOLD STILL')}")
        return {"segment": segment}

    return tracefile.record(
        path,
        duration=seconds,
        label=label,
        instructions=[
            f"1. HOLD STILL for {half:.0f} s — hand in front of the camera, as steady as you can.",
            f"2. SWEEP for {half:.0f} s — move smoothly left to right and back, at a normal pace.",
        ],
        width=width,
        height=height,
        camera_index=camera_index,
    )


def synthesize(path: Path, *, seconds: float, fps: float, sigma: float, seed: int) -> int:
    """A trace with known noise, so the tool can be checked without a camera.

    Useful for verifying the measurement itself: the rest segment has exactly `sigma` of Gaussian
    noise per landmark, so a filter that claims to cut jitter by 3x can be held to it.
    """
    from airhand.handmodel import POINTING, make_hand

    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    half = seconds / 2
    step = 1.0 / fps

    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "kind": TRACE_KIND,
                    "version": TRACE_VERSION,
                    "frameWidth": 640,
                    "frameHeight": 480,
                    "synthetic": {"sigma": sigma, "seed": seed},
                }
            )
            + "\n"
        )

        elapsed = 0.0
        while elapsed < seconds:
            if elapsed < half:
                segment, centre = REST, (0.5, 0.6)
            else:
                # A smooth there-and-back sweep, so lag has a direction to be measured against.
                phase = (elapsed - half) / half
                segment = SWEEP
                centre = (0.5 + 0.18 * math.sin(phase * 2 * math.pi), 0.6)
            hand = make_hand(POINTING, center=centre, pinch_index=0.9)
            noisy = [
                [x + rng.gauss(0, sigma), y + rng.gauss(0, sigma), z] for x, y, z in hand
            ]
            handle.write(
                json.dumps({"t": elapsed, "segment": segment, "landmarks": noisy}) + "\n"
            )
            elapsed += step

    print(f"Wrote a synthetic trace to {path} (sigma {sigma}, {fps:.0f} fps).")
    return 0


# ---------------------------------------------------------------------------- replay


def _as_index_anchor(landmarks: list[list[float]]) -> list[list[float]]:
    """Collapse the palm onto the index knuckle.

    Makes `PointerTracker` report the old single-landmark anchor without a second code path: the
    palm centroid of five identical points is that point. Comparing the two through the same code
    is the whole point — a re-implementation here could flatter either side.
    """
    doctored = [list(point) for point in landmarks]
    knuckle = doctored[INDEX_MCP]
    for index in PALM_LANDMARKS:
        doctored[index] = list(knuckle)
    return doctored


def _screen_series(
    frames: list[dict],
    *,
    config: PointerConfig | None,
    screen: ScreenSize,
    frame_aspect: float,
    coverage: float,
    index_anchor: bool = False,
) -> list[tuple[str, tuple[int, int]]]:
    """Run one candidate over the trace and return (segment, screen pixel) per detected frame.

    `config=None` means no smoothing at all — the raw anchor, mapped. That row is the baseline
    every other row has to beat.
    """
    area = active_area_for(
        screen_aspect=screen.aspect, frame_aspect=frame_aspect, coverage=coverage
    )
    tracker = PointerTracker(config) if config is not None else None
    series: list[tuple[str, tuple[int, int]]] = []

    for frame in frames:
        landmarks = frame["landmarks"]
        usable = landmarks is not None and len(landmarks) >= LANDMARKS_REQUIRED
        if usable and index_anchor:
            landmarks = _as_index_anchor(landmarks)

        if tracker is not None:
            # Same admission rule as the tracker's own, so every row skips the same frames and the
            # series stay index-aligned for the lag comparison.
            position = tracker.update(landmarks, now=frame["t"])
        else:
            position = palm_center(landmarks) if usable else None

        if position is None:
            continue
        series.append(
            (
                frame["segment"],
                to_screen(
                    position[0],
                    position[1],
                    area=area,
                    screen_width=screen.width,
                    screen_height=screen.height,
                ),
            )
        )
    return series


# The reference path is a rolling median of the raw anchor. Half a second is long enough to
# average away detector noise and short enough to follow a hand that is drifting, which every
# hand does.
REFERENCE_WINDOW_SECONDS = 0.5


def _reference_path(points: list[tuple[int, int]], fps: float) -> list[tuple[float, float]]:
    """Best estimate of where the hand actually was, one entry per input point."""
    half = max(1, round(REFERENCE_WINDOW_SECONDS * fps / 2))
    reference: list[tuple[float, float]] = []
    for index in range(len(points)):
        window = points[max(0, index - half) : index + half + 1]
        reference.append(
            (statistics.median(x for x, _ in window), statistics.median(y for _, y in window))
        )
    return reference


def _rest_jitter(
    series: list[tuple[str, tuple[int, int]]],
    baseline: list[tuple[str, tuple[int, int]]],
    fps: float,
) -> tuple[float, float]:
    """RMS and p95 of how far the cursor sits from where the hand actually was, in pixels.

    Measured against a **rolling** median of the raw anchor, not a single fixed point.

    The fixed-point version was the first thing this tool did and it was wrong. A hand held still
    in mid-air still drifts several pixels' worth over ten seconds — measured on a real trace, five
    times more than the per-frame noise — so the number it produced was mostly a report on how
    steady the person was, and it barely moved between filter settings. Worse, it ranked heavy
    smoothing *below* light smoothing, because a heavy filter lags behind the drift and therefore
    sits further from the average position. That is backwards for the thing we are tuning.

    Against a reference that tracks the drift, what is left is what the filter can actually do
    something about: detector noise, plus the lag it costs to remove it.
    """
    points = [point for segment, point in series if segment == REST]
    truth = [point for segment, point in baseline if segment == REST]
    if len(points) < 10 or len(points) != len(truth):
        return (float("nan"), float("nan"))

    reference = _reference_path(truth, fps)
    errors = sorted(
        math.hypot(point[0] - ref[0], point[1] - ref[1]) for point, ref in zip(points, reference)
    )
    rms = math.sqrt(sum(error * error for error in errors) / len(errors))
    return (rms, errors[int(0.95 * (len(errors) - 1))])


def _sweep_lag(
    filtered: list[tuple[str, tuple[int, int]]], raw: list[tuple[str, tuple[int, int]]]
) -> float:
    """Mean distance the filtered cursor trails the raw one *along the direction of travel*.

    Measured as a projection rather than as plain distance, because plain distance cannot tell lag
    from noise — and noise is what we are deliberately adding here. A negative number would mean
    the filter runs ahead of the hand, which nothing here can do.
    """
    pairs = [
        (f[1], r[1]) for f, r in zip(filtered, raw) if f[0] == SWEEP and r[0] == SWEEP
    ]
    if len(pairs) < 10:
        return float("nan")

    lags: list[float] = []
    for index in range(1, len(pairs)):
        (_, raw_now), (_, raw_before) = (pairs[index], pairs[index - 1])
        dx = raw_now[0] - raw_before[0]
        dy = raw_now[1] - raw_before[1]
        speed = math.hypot(dx, dy)
        if speed < 1.0:  # standing still tells us nothing about lag
            continue
        filtered_now = pairs[index][0]
        lags.append(((raw_now[0] - filtered_now[0]) * dx + (raw_now[1] - filtered_now[1]) * dy) / speed)

    return statistics.fmean(lags) if lags else float("nan")


# The range runs well past anything plausible in both directions on purpose. A grid whose best row
# is its last row has not found an optimum, it has found its own edge — so it has to extend until
# the numbers turn back, and here they do at both ends.
CANDIDATES: list[tuple[str, PointerConfig]] = [
    (f"min_cutoff {cutoff} / beta {beta}", PointerConfig(min_cutoff=cutoff, beta=beta))
    for cutoff in (0.4, 0.6, 0.8, 1.2, 1.5, 2.0, 3.0, 4.0)
    for beta in (3.0, 6.0, 10.0)
]


def replay(path: Path, *, screen: ScreenSize, coverage: float) -> int:
    trace = tracefile.load(path)
    frames = trace.frames
    detected = [frame for frame in frames if frame["landmarks"]]
    if not detected:
        raise SystemExit("the trace contains no detected hands")

    frame_aspect = trace.frame_aspect
    fps = trace.fps

    print(f"{path}")
    print(
        f"  {len(frames)} frames, {len(detected)} with a hand, {fps:.1f} fps, "
        f"frame {trace.header['frameWidth']}x{trace.header['frameHeight']}"
    )
    print(f"  mapped onto {screen.width}x{screen.height} at coverage {coverage}")
    print()

    def series_for(config: PointerConfig | None, index_anchor: bool) -> list:
        return _screen_series(
            frames,
            config=config,
            screen=screen,
            frame_aspect=frame_aspect,
            coverage=coverage,
            index_anchor=index_anchor,
        )

    # One unfiltered baseline per anchor. Lag has to be measured against the *same* anchor, or the
    # fixed offset between the knuckle and the palm centre is read as lag — which on the first run
    # of this tool produced a filter that appeared to run 19 px ahead of the hand.
    baselines = {False: series_for(None, False), True: series_for(None, True)}

    def evaluate(label: str, config: PointerConfig | None, *, index_anchor: bool = False) -> None:
        series = baselines[index_anchor] if config is None else series_for(config, index_anchor)
        jitter, p95 = _rest_jitter(series, baselines[index_anchor], fps)
        lag = _sweep_lag(series, baselines[index_anchor])
        print(f"  {label:34} {jitter:7.1f} {p95:7.1f} {lag:8.1f}")

    print(f"  {'':34} {'jitter':>7} {'p95':>7} {'lag':>8}")
    print(f"  {'':34} {'px':>7} {'px':>7} {'px':>8}")
    print("  " + "-" * 58)

    evaluate("raw palm centre, no filter", None)
    evaluate("raw index knuckle, no filter", None, index_anchor=True)
    print("  " + "-" * 58)
    evaluate("default + index knuckle anchor", PointerConfig(), index_anchor=True)
    print("  " + "-" * 58)
    for label, config in CANDIDATES:
        evaluate(label, config)

    print()
    print("  jitter/p95: how far the cursor sits from where the hand actually was, at rest.")
    print("              Measured against a rolling median, so your hand may drift — it will.")
    print("  lag: how far it trails the hand mid-sweep. Lower is better. They trade off.")
    return 0


# ------------------------------------------------------------------------------ cli


def _screen_for(argument: str | None) -> ScreenSize:
    if argument:
        width, _, height = argument.lower().partition("x")
        return ScreenSize(int(width), int(height))
    try:
        return primary_screen()
    except ScreenUnavailable:
        return FALLBACK_SCREEN


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench_pointer", description=__doc__)
    parser.add_argument("--record", type=Path, help="Record a trace to this path.")
    parser.add_argument("--replay", type=Path, help="Evaluate configurations against a trace.")
    parser.add_argument("--synthetic", type=Path, help="Write a synthetic trace to this path.")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--camera-width", type=int, default=640)
    parser.add_argument("--camera-height", type=int, default=480)
    parser.add_argument("--fps", type=float, default=30.0, help="Synthetic trace frame rate.")
    parser.add_argument("--sigma", type=float, default=0.004, help="Synthetic per-landmark noise.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--screen", default=None, help="Override the screen size, e.g. 2560x1080."
    )
    parser.add_argument("--coverage", type=float, default=0.7)
    args = parser.parse_args(argv)

    if args.record:
        return record(
            args.record,
            seconds=args.seconds,
            width=args.camera_width,
            height=args.camera_height,
            camera_index=args.camera_index,
        )
    if args.synthetic:
        return synthesize(
            args.synthetic, seconds=args.seconds, fps=args.fps, sigma=args.sigma, seed=args.seed
        )
    if args.replay:
        return replay(args.replay, screen=_screen_for(args.screen), coverage=args.coverage)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
