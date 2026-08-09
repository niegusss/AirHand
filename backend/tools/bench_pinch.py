r"""Why a pinch did not become a click.

"Sometimes it doesn't register" is not something you can fix by staring at thresholds. This records
a series of deliberate pinches and says what happened to **each one**: how close the fingers got as
the engine measured it, whether detection dropped out mid-pinch, and what the real Gesture Engine
decided. Failures stop being anecdotes and become a count with reasons.

    cd backend

    # Engine must be stopped first — OpenCV holds the webcam exclusively on Windows.
    .\.venv\Scripts\python.exe tools\bench_pinch.py --record traces\pinch.jsonl --attempts 20
    .\.venv\Scripts\python.exe tools\bench_pinch.py --replay traces\pinch.jsonl

The replay runs the trace through the same `LandmarkFilter` -> `GestureEngine` chain as `live.py`,
twice: once with the dropout grace disabled and once with it on. **One recording therefore both
diagnoses the problem and proves the fix**, with no second wave at the webcam and no "feels better".
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tracefile  # noqa: E402 - sibling module; tools/ is on sys.path for scripts run from it
from airhand.filters import LandmarkFilter  # noqa: E402
from airhand.gestures import GestureConfig, GestureEngine, extract  # noqa: E402
from airhand.protocol import landmark_count  # noqa: E402

PINCH = "pinch"


@dataclass
class Attempt:
    """One prompted pinch, and everything that could explain its outcome."""

    index: int
    frames: int = 0
    gaps: int = 0
    """Smallest thumb-to-index distance the engine saw, before and after smoothing. The gap between
    them is what the filter costs: it is heaviest exactly at contact, where finger speed drops to
    zero, so it can shave the extremum off a quick tap."""
    min_raw: float = float("inf")
    min_filtered: float = float("inf")
    closed_first_at: float | None = None
    closed_last_at: float | None = None
    events: list[str] = field(default_factory=list)
    """Both pinch pairs closed at once.

    Written as bait for a defect that was still latent, and which `bench_poses.py` caught on
    2026-08-09: the release blocks in `_classify` were independent, so one gesture emitted a left
    *and* a right click. `GestureEngine._owner` now arbitrates, and this count stays as the signal
    that the arbitration is being exercised rather than skipped.
    """
    both_closed: bool = False

    @property
    def held(self) -> float:
        if self.closed_first_at is None or self.closed_last_at is None:
            return 0.0
        return self.closed_last_at - self.closed_first_at

    def verdict(self) -> str:
        if "left_click" in self.events:
            return "click"
        if "drag_start" in self.events:
            return "drag"
        if "right_click" in self.events:
            return "right"
        return "nothing"


def _run(trace: tracefile.Trace, config: GestureConfig) -> dict[int, Attempt]:
    """One continuous pass over the trace, exactly as the pipeline would see it.

    Deliberately *not* reset between attempts: the classifier is a state machine, and resetting it
    per attempt would hide precisely the class of bug this tool is looking for.
    """
    landmark_filter = LandmarkFilter(count=landmark_count())
    engine = GestureEngine(config=config)
    aspect = trace.frame_aspect

    attempts: dict[int, Attempt] = {}
    previous_t: float | None = None

    for frame in trace.frames:
        index = int(frame.get("attempt", 0))
        attempt = attempts.setdefault(index, Attempt(index))
        attempt.frames += 1

        now = frame["t"]
        dt = 0.0 if previous_t is None else now - previous_t
        previous_t = now

        raw = frame["landmarks"]
        if raw is None:
            attempt.gaps += 1
            landmark_filter.reset()
            update = engine.update(None, aspect=aspect, now=now)
            attempt.events.extend(event.type.value for event in update.events)
            continue

        raw_features = extract(
            raw, aspect=aspect, extended_angle_degrees=config.extended_angle_degrees
        )
        if raw_features is not None:
            attempt.min_raw = min(attempt.min_raw, raw_features.pinch_index)

        smoothed = landmark_filter.filter(raw, dt) if dt > 0 else raw
        update = engine.update(smoothed, aspect=aspect, now=now)
        attempt.events.extend(event.type.value for event in update.events)

        debug = update.debug
        if debug is not None:
            attempt.min_filtered = min(attempt.min_filtered, debug.pinch_index)
            if debug.pinch_index < config.pinch_close and debug.pinch_middle < config.pinch_close:
                attempt.both_closed = True
            if debug.state in ("pinch_index_pending", "drag"):
                if attempt.closed_first_at is None:
                    attempt.closed_first_at = now
                attempt.closed_last_at = now

    return attempts


def _reason(attempt: Attempt, config: GestureConfig) -> str:
    """Why this attempt produced nothing. Order matters: report the first thing that broke."""
    if attempt.min_filtered >= config.pinch_close:
        return "never crossed pinch_close"
    if attempt.gaps:
        return "lost the pinch to a detection gap"
    return "unexplained"


def replay(path: Path, *, grace: float) -> int:
    trace = tracefile.load(path)
    if not trace.frames:
        raise SystemExit("the trace is empty")

    detected = sum(1 for frame in trace.frames if frame["landmarks"])
    without = GestureConfig(dropout_grace_seconds=0.0)
    with_grace = GestureConfig(dropout_grace_seconds=grace)

    before = _run(trace, without)
    after = _run(trace, with_grace)

    print(f"{path}")
    print(
        f"  {len(trace.frames)} frames, {detected} with a hand, {trace.fps:.1f} fps, "
        f"{len(before)} attempts"
    )
    print(f"  pinch closes below {without.pinch_close}, opens above {without.pinch_open}")
    print()
    print("    #   min pinch    gaps   held      verdict")
    print("        raw   filt                    grace 0.00   " f"grace {grace:.2f}")
    print("  " + "-" * 62)

    for index in sorted(before):
        old, new = before[index], after[index]
        raw = "  —  " if old.min_raw == float("inf") else f"{old.min_raw:5.2f}"
        filtered = "  —  " if old.min_filtered == float("inf") else f"{old.min_filtered:5.2f}"
        changed = "*" if old.verdict() != new.verdict() else " "
        print(
            f"  {index + 1:3}  {raw} {filtered}  {old.gaps:5}  {old.held:5.2f}     "
            f"{old.verdict():<12} {new.verdict():<9}{changed}"
        )

    print()
    for label, attempts, config in (
        ("grace 0.00", before, without),
        (f"grace {grace:.2f}", after, with_grace),
    ):
        counts = {"click": 0, "drag": 0, "right": 0, "nothing": 0}
        for attempt in attempts.values():
            counts[attempt.verdict()] += 1
        print(
            f"  {label}:  {counts['click']} click, {counts['drag']} drag, "
            f"{counts['right']} right, {counts['nothing']} nothing"
        )
        failures: dict[str, int] = {}
        for attempt in attempts.values():
            if attempt.verdict() == "nothing":
                reason = _reason(attempt, config)
                failures[reason] = failures.get(reason, 0) + 1
        for reason, count in sorted(failures.items(), key=lambda item: -item[1]):
            print(f"      {count} × {reason}")

    both = sum(1 for attempt in before.values() if attempt.both_closed)
    if both:
        print()
        print(
            f"  {both} attempt(s) closed BOTH pinch pairs at once — a left and a right click can "
            "fire from one gesture."
        )
    return 0


def record(path: Path, *, attempts: int, interval: float, camera_index: int) -> int:
    """A metronome, so every attempt has a known window.

    Prompted and timed rather than segmented from the signal afterwards: the boundaries have to be
    independent of the detection being analysed, or a missed pinch could also lose its own window.
    """
    announced: set[int] = set()

    def label(elapsed: float) -> dict[str, object]:
        index = min(int(elapsed / interval), attempts - 1)
        if index not in announced:
            announced.add(index)
            print(f"  PINCH  #{index + 1}")
        return {"segment": PINCH, "attempt": index}

    return tracefile.record(
        path,
        duration=attempts * interval,
        label=label,
        instructions=[
            f"{attempts} pinches, one every {interval:.0f} s, on the prompt.",
            "Touch thumb to index and release, at whatever pace you normally would.",
            "Keep the hand in frame between attempts — the gaps between them matter too.",
        ],
        camera_index=camera_index,
    )


def synthesize(
    path: Path, *, attempts: int, interval: float, fps: float, drop_rate: float, seed: int
) -> int:
    """A scripted trace with deliberate dropouts, so the tool can be checked without a camera.

    Every attempt is a clean pinch that *should* resolve to a click. The only thing standing in the
    way is the injected detection gap, so the report has exactly one story to tell and any other
    answer is the tool's own bug.
    """
    import json
    import random

    from airhand.handmodel import POINTING, make_hand

    rng = random.Random(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    step = 1.0 / fps

    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "kind": tracefile.TRACE_KIND,
                    "version": tracefile.TRACE_VERSION,
                    "frameWidth": 640,
                    "frameHeight": 480,
                    "synthetic": {"dropRate": drop_rate, "seed": seed},
                }
            )
            + "\n"
        )

        elapsed = 0.0
        while elapsed < attempts * interval:
            index = min(int(elapsed / interval), attempts - 1)
            within = elapsed - index * interval
            # Closed for 0.2 s in the middle of each window — a short tap, comfortably a click.
            closed = 0.6 <= within < 0.8
            landmarks = make_hand(POINTING, pinch_index=0.15 if closed else 0.9)
            # Fingers overlapping is exactly when the detector struggles, so drop only then.
            if closed and rng.random() < drop_rate:
                landmarks = None
            handle.write(
                json.dumps(
                    {"t": elapsed, "segment": PINCH, "attempt": index, "landmarks": landmarks}
                )
                + "\n"
            )
            elapsed += step

    print(f"Wrote a synthetic pinch trace to {path} ({attempts} attempts, drop rate {drop_rate}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench_pinch", description=__doc__)
    parser.add_argument("--record", type=Path, help="Record a pinch trace to this path.")
    parser.add_argument("--replay", type=Path, help="Analyse a recorded pinch trace.")
    parser.add_argument("--synthetic", type=Path, help="Write a scripted trace to this path.")
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between prompts.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--fps", type=float, default=30.0, help="Synthetic trace frame rate.")
    parser.add_argument(
        "--drop-rate", type=float, default=0.25, help="Synthetic chance of a lost frame per pinch."
    )
    parser.add_argument("--seed", type=int, default=5)
    parser.add_argument(
        "--grace",
        type=float,
        default=GestureConfig().dropout_grace_seconds,
        help="Dropout grace to compare against zero.",
    )
    args = parser.parse_args(argv)

    if args.record:
        return record(
            args.record,
            attempts=args.attempts,
            interval=args.interval,
            camera_index=args.camera_index,
        )
    if args.synthetic:
        return synthesize(
            args.synthetic,
            attempts=args.attempts,
            interval=args.interval,
            fps=args.fps,
            drop_rate=args.drop_rate,
            seed=args.seed,
        )
    if args.replay:
        return replay(args.replay, grace=args.grace)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
