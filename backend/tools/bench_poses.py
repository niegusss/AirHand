r"""Which poses trip the pinch thresholds — including the ones nobody meant as a click.

`bench_pinch.py` answers "why did this pinch not register". This answers the opposite question:
**what registers that should not**, and whether one threshold can serve both pinch pairs at once.

Two failures found on 2026-08-09 motivated it, and both are invisible to a trace of deliberate
index pinches:

- a right click made with the index finger curled closes the *index* pair too, because the two
  fingertips sit beside each other — one gesture, two clicks;
- a plain fist may read below `pinch_close`, so closing the hand is a click. Every threshold in
  this project was reasoned against the **open**-hand floor; the closed-hand floor was never
  measured.

So the recording deliberately includes poses that are not clicks. A trace containing only clicks
can only ever confirm that clicks work.

    cd backend

    # Engine must be stopped first — OpenCV holds the webcam exclusively on Windows.
    .\.venv\Scripts\python.exe tools\bench_poses.py --record traces\poses.jsonl
    .\.venv\Scripts\python.exe tools\bench_poses.py --replay traces\poses.jsonl

The replay ends in a sweep: for each candidate `pinch_close`, what the **real** Gesture Engine
emits in every segment. That table is what picks the threshold — a number chosen from the pinch
segments alone is exactly how the current one got too high.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tracefile  # noqa: E402 - sibling module; tools/ is on sys.path for scripts run from it
from airhand.filters import LandmarkFilter  # noqa: E402
from airhand.gestures import GestureConfig, GestureEngine, extract  # noqa: E402
from airhand.protocol import landmark_count  # noqa: E402


@dataclass(frozen=True)
class Segment:
    """One prompted stretch of the recording."""

    name: str
    reps: int
    prompt: str
    """What we are trying to learn from it — printed in the replay so the report explains itself."""
    question: str
    """True when a click here would be wrong. The whole point of the non-click segments."""
    silent: bool
    """Which event this segment is deliberately producing, if any. Used at segment boundaries."""
    button: str | None = None


# Ordered as recorded. `rest` first so the hand is settled before anything is asked of it.
SCHEDULE: tuple[Segment, ...] = (
    Segment("rest", 3, "hold your hand comfortably in view, no gesture", "baseline", True),
    Segment(
        "fist",
        5,
        "close your hand into a fist, then open it",
        "the closed-hand floor — the number nobody has ever measured",
        True,
    ),
    Segment(
        "left",
        8,
        "LEFT click: touch thumb to index finger, release",
        "real left clicks",
        False,
        button="left_click",
    ),
    Segment(
        "right",
        8,
        "RIGHT click: touch thumb to MIDDLE finger, release — hold your hand naturally",
        "does a right click also close the index pair",
        False,
        button="right_click",
    ),
    Segment(
        "scroll", 3, "scroll pose: index and middle straight, others folded", "must stay silent", True
    ),
)

SECONDS_PER_REP = 2.0

"""How long after a prompt change a click may still belong to the previous prompt.

The schedule advances on a clock; the hand does not. A click is defined by its *release*, so a
pinch begun near the end of one segment resolves inside the next one — measured at up to 0.33 s
past the boundary on `traces/poses.jsonl`. Attributed naively, that lands as a phantom click in a
segment that is supposed to be silent, and the tool then calls a working threshold unusable.
"""
BOUNDARY_SECONDS = 0.5


@dataclass
class SegmentStats:
    """What the engine saw during one segment, and what it did about it."""

    name: str
    frames: int = 0
    detected: int = 0
    index: list[float] = field(default_factory=list)
    middle: list[float] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    """Frames where both pinch pairs were closed at once — the left/right cross-talk, counted."""
    both_closed: int = 0
    """Events recorded here that arrived after this segment's own frames — see `_attribute`."""
    borrowed: int = 0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(fraction * (len(ordered) - 1))))]


def _attribute(
    event: str,
    stats: dict[str, SegmentStats],
    segment: SegmentStats,
    previous: Segment | None,
    since_boundary: float,
) -> None:
    """Credit one event to the segment whose gesture produced it.

    Almost always that is the segment the frame is labelled with. The exception is the seam: a
    click released just after the prompt changed was made under the previous prompt, and counting
    it against the new one invents a phantom click in a segment meant to be silent.

    **The exception is kept as narrow as it can be**, because a wide one would hide exactly the
    defects this tool exists to find. All three must hold: inside `BOUNDARY_SECONDS` of the
    change, the previous segment was deliberately producing a button, and it is *that* button.
    A spurious left click at the start of `fist` after the `left` segment is the one case this
    cannot tell apart — it is charged to `left`, and the sweep says so out loud.
    """
    if (
        since_boundary < BOUNDARY_SECONDS
        and previous is not None
        and previous.button == event
    ):
        owner = stats.setdefault(previous.name, SegmentStats(previous.name))
        owner.events.append(event)
        owner.borrowed += 1
        return
    segment.events.append(event)


def _run(trace: tracefile.Trace, config: GestureConfig) -> dict[str, SegmentStats]:
    """One continuous pass, exactly as `live.py` would see it.

    Never reset between segments: the classifier is a state machine, and resetting it per segment
    would hide the bugs that live in the seams between gestures.
    """
    landmark_filter = LandmarkFilter(count=landmark_count())
    engine = GestureEngine(config=config)
    aspect = trace.frame_aspect
    by_name = {segment.name: segment for segment in SCHEDULE}

    stats: dict[str, SegmentStats] = {}
    previous_t: float | None = None
    current_name: str | None = None
    previous_segment: Segment | None = None
    segment_started_at = 0.0

    for frame in trace.frames:
        name = str(frame.get("segment", "?"))
        now = frame["t"]

        if name != current_name:
            previous_segment = by_name.get(current_name) if current_name is not None else None
            current_name = name
            segment_started_at = now

        segment = stats.setdefault(name, SegmentStats(name))
        segment.frames += 1
        since_boundary = now - segment_started_at

        dt = 0.0 if previous_t is None else now - previous_t
        previous_t = now

        raw = frame["landmarks"]
        if raw is None:
            landmark_filter.reset()
            update = engine.update(None, aspect=aspect, now=now)
            for event in update.events:
                _attribute(event.type.value, stats, segment, previous_segment, since_boundary)
            continue

        segment.detected += 1
        smoothed = landmark_filter.filter(raw, dt) if dt > 0 else raw
        update = engine.update(smoothed, aspect=aspect, now=now)
        for event in update.events:
            _attribute(event.type.value, stats, segment, previous_segment, since_boundary)

        debug = update.debug
        if debug is not None:
            # The filtered values, because those are what the thresholds actually see.
            segment.index.append(debug.pinch_index)
            segment.middle.append(debug.pinch_middle)
            if debug.pinch_index < config.pinch_close and debug.pinch_middle < config.pinch_close:
                segment.both_closed += 1

    return stats


def _distances(stats: dict[str, SegmentStats]) -> None:
    print("  distances the engine sees, per segment (filtered, in hand-scale units)")
    print()
    print(f"    {'segment':<8} {'n':>5}   {'index min':>9} {'p02':>6} {'median':>7}"
          f"   {'middle min':>10} {'p02':>6} {'median':>7}")
    print("    " + "-" * 72)
    for segment in SCHEDULE:
        found = stats.get(segment.name)
        if found is None or not found.index:
            print(f"    {segment.name:<8} {'—':>5}   (not in this trace)")
            continue
        print(
            f"    {segment.name:<8} {len(found.index):>5}   "
            f"{min(found.index):>9.3f} {_percentile(found.index, 0.02):>6.3f} "
            f"{statistics.median(found.index):>7.3f}   "
            f"{min(found.middle):>10.3f} {_percentile(found.middle, 0.02):>6.3f} "
            f"{statistics.median(found.middle):>7.3f}"
        )


def _sweep(trace: tracefile.Trace, candidates: list[float], band: float, hold: float) -> None:
    """What the real engine emits at each candidate threshold, per segment.

    The columns that matter are the *silent* segments. A threshold is only usable if `rest`, `fist`
    and `scroll` stay at zero — a click nobody asked for costs more than a click that was missed,
    because the user cannot see why it happened.
    """
    print("  what the engine emits at each candidate pinch_close")
    print(f"  (band held at {band:.2f}, so pinch_open moves with it; hold_to_drag {hold:.2f}s)")
    print()
    header = f"    {'close':>6}"
    for segment in SCHEDULE:
        header += f" {segment.name:>13}"
    header += "   verdict"
    print(header)
    print("    " + "-" * (6 + 14 * len(SCHEDULE) + 12))

    for close in candidates:
        stats = _run(
            trace,
            GestureConfig(
                pinch_close=close, pinch_open=close + band, hold_to_drag_seconds=hold
            ),
        )
        row = f"    {close:>6.3f}"
        noise = 0
        crosstalk = 0
        left = right = 0
        for segment in SCHEDULE:
            found = stats.get(segment.name)
            if found is None:
                row += f" {'—':>13}"
                continue
            clicks = sum(1 for e in found.events if e in ("left_click", "right_click"))
            drags = sum(1 for e in found.events if e == "drag_start")
            lefts = sum(1 for e in found.events if e == "left_click")
            rights = sum(1 for e in found.events if e == "right_click")
            if segment.silent:
                noise += clicks + drags
                cell = "." if clicks + drags == 0 else f"{clicks + drags} SPURIOUS"
            elif segment.name == "left":
                left = lefts
                crosstalk += rights
                cell = f"{lefts}L" + (f" +{rights}R" if rights else "")
            else:
                right = rights
                crosstalk += lefts
                cell = f"{rights}R" + (f" +{lefts}L" if lefts else "")
            row += f" {cell:>13}"

        expected_left = next(s.reps for s in SCHEDULE if s.name == "left")
        expected_right = next(s.reps for s in SCHEDULE if s.name == "right")
        # Ordered by how much each failure costs the user. A click nobody asked for is worst: it
        # lands somewhere and cannot be explained. Cross-talk is next — the gesture was made, and
        # the wrong button came out of it, which is a click *and* a wrong click.
        if noise:
            verdict = "unusable — spurious"
        elif crosstalk:
            verdict = f"unusable — {crosstalk} wrong button"
        elif left >= expected_left and right >= expected_right:
            verdict = "clean"
        else:
            # Clamped: a segment can overshoot (one rep that closed twice), and "misses -1L" reads
            # like a defect in the row rather than a count of what did not happen.
            verdict = (
                f"misses {max(0, expected_left - left)}L {max(0, expected_right - right)}R"
            )
        print(row + f"   {verdict}")


def replay(path: Path, *, close: float, band: float, hold: float) -> int:
    trace = tracefile.load(path)
    if not trace.frames:
        raise SystemExit("the trace is empty")

    config = GestureConfig(
        pinch_close=close, pinch_open=close + band, hold_to_drag_seconds=hold
    )
    stats = _run(trace, config)

    detected = sum(1 for frame in trace.frames if frame["landmarks"])
    print(f"{path}")
    print(f"  {len(trace.frames)} frames, {detected} with a hand, {trace.fps:.1f} fps")
    print()
    for segment in SCHEDULE:
        print(f"    {segment.name:<8} {segment.question}")
    print()

    _distances(stats)
    print()

    print(
        f"  at the profile's own threshold (close {close:.4f}, open {close + band:.4f}, "
        f"hold {hold:.2f}s)"
    )
    print()
    for segment in SCHEDULE:
        found = stats.get(segment.name)
        if found is None:
            continue
        counts: dict[str, int] = {}
        for event in found.events:
            counts[event] = counts.get(event, 0) + 1
        summary = ", ".join(f"{count}× {name}" for name, count in sorted(counts.items())) or "nothing"
        flag = "  <-- SPURIOUS" if segment.silent and counts else ""
        print(f"    {segment.name:<8} {summary}{flag}")
        if found.borrowed:
            # Said out loud, because it is the one place the tool moves a number from the segment
            # it was recorded in. Silently corrected counts are how a measurement stops being one.
            print(
                f"             {found.borrowed} of these released after the prompt had already "
                f"changed (within {BOUNDARY_SECONDS:.1f}s) — counted here, where the gesture "
                "was made"
            )
        if found.both_closed:
            # Not a failure by itself — it is the condition arbitration exists to handle, and
            # seeing it is how you know the arbitration is being exercised rather than skipped.
            print(
                f"             {found.both_closed} frame(s) closed BOTH pairs at once — "
                "arbitration decided which one this was"
            )
    print()

    # Fine where the answer lives and coarse above it. Everything that decides a usable threshold
    # for a closed hand sits between 0.10 and 0.20 — the old grid started at 0.15 and stepped 0.05,
    # which is three points across the entire region of interest.
    candidates = [round(0.100 + 0.025 * step, 3) for step in range(9)]
    candidates += [round(0.35 + 0.05 * step, 2) for step in range(6)]
    # The shipped default always gets a row, wherever the grid happens to fall. It is the value
    # every uncalibrated hand — and every "Restore defaults" — actually runs on.
    candidates = sorted({*candidates, GestureConfig().pinch_close, close})
    _sweep(trace, candidates, band, hold)
    return 0


def record(path: Path, *, camera_index: int, seconds_per_rep: float) -> int:
    """A metronome per segment, so every rep has a window independent of the signal.

    Same reasoning as `bench_pinch.record`: boundaries derived from the detection would lose their
    own window whenever the detection is what failed.
    """
    plan: list[tuple[float, Segment, int]] = []
    clock = 0.0
    for segment in SCHEDULE:
        for rep in range(segment.reps):
            plan.append((clock, segment, rep))
            clock += seconds_per_rep
    duration = clock
    announced: set[tuple[str, int]] = set()

    def label(elapsed: float) -> dict[str, object]:
        current = plan[0]
        for entry in plan:
            if elapsed >= entry[0]:
                current = entry
        _, segment, rep = current
        key = (segment.name, rep)
        if key not in announced:
            announced.add(key)
            print(f"  {segment.name.upper():<7} {rep + 1}/{segment.reps}   {segment.prompt}")
        return {"segment": segment.name, "attempt": rep}

    return tracefile.record(
        path,
        duration=duration,
        label=label,
        instructions=[
            f"About {duration:.0f} seconds. Follow each prompt as it appears.",
            "Keep your hand in frame throughout — the quiet stretches are measurements too.",
            "Move naturally. A pose held unnaturally still is a pose you will never make again.",
        ],
        camera_index=camera_index,
    )


def synthesize(path: Path, *, fps: float) -> int:
    """A scripted trace, so the tool can be checked without a camera.

    Built from `handmodel`, which is the same geometry the tests and the synthetic source use. It
    reproduces both known failures on purpose: the `fist` segment reads below the shipped threshold,
    and the `right` segment curls the index so both pairs close.
    """
    import json

    from airhand.handmodel import FIST, POINTING, SCROLL_POSE, make_hand

    path.parent.mkdir(parents=True, exist_ok=True)
    step = 1.0 / fps

    def pose_for(segment: str, within: float) -> list[list[float]]:
        # 0.25 s, comfortably under `hold_to_drag_seconds`. A window sitting exactly on that
        # boundary would make the tool's own self-check flip between click and drag for reasons
        # that have nothing to do with what it is measuring.
        active = 0.6 <= within < 0.85
        if segment == "rest":
            return make_hand(POINTING)
        if segment == "fist":
            return make_hand(FIST if active else POINTING)
        if segment == "left":
            return make_hand(POINTING, pinch_index=0.15 if active else 0.9)
        if segment == "right":
            # Index curled — what a hand does when the thumb reaches the middle finger.
            return make_hand(FIST if active else POINTING, pinch_middle=0.15 if active else 1.1)
        return make_hand(SCROLL_POSE)

    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "kind": tracefile.TRACE_KIND,
                    "version": tracefile.TRACE_VERSION,
                    "frameWidth": 640,
                    "frameHeight": 480,
                    "synthetic": {"schedule": [s.name for s in SCHEDULE]},
                }
            )
            + "\n"
        )

        elapsed = 0.0
        for segment in SCHEDULE:
            for rep in range(segment.reps):
                started = elapsed
                while elapsed - started < SECONDS_PER_REP:
                    handle.write(
                        json.dumps(
                            {
                                "t": round(elapsed, 4),
                                "segment": segment.name,
                                "attempt": rep,
                                "landmarks": pose_for(segment.name, elapsed - started),
                            }
                        )
                        + "\n"
                    )
                    elapsed += step

    print(f"Wrote a synthetic pose trace to {path}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench_poses", description=__doc__)
    parser.add_argument("--record", type=Path, help="Record a pose trace to this path.")
    parser.add_argument("--replay", type=Path, help="Analyse a recorded pose trace.")
    parser.add_argument("--synthetic", type=Path, help="Write a scripted trace to this path.")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--fps", type=float, default=30.0, help="Synthetic trace frame rate.")
    parser.add_argument(
        "--seconds-per-rep", type=float, default=SECONDS_PER_REP, help="Window per prompt."
    )
    parser.add_argument(
        "--close",
        type=float,
        default=GestureConfig().pinch_close,
        help="Threshold to report against. Pass your profile's value.",
    )
    parser.add_argument(
        "--band",
        type=float,
        default=GestureConfig().pinch_open - GestureConfig().pinch_close,
        help="Hysteresis band; pinch_open is close + band.",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=GestureConfig().hold_to_drag_seconds,
        # Pass your profile's value. It changes what the *left* column can be — past this, a pinch
        # is a drag — so a sweep run at a different hold is answering a different question.
        help="hold_to_drag_seconds to replay at.",
    )
    args = parser.parse_args(argv)

    if args.record:
        return record(
            args.record, camera_index=args.camera_index, seconds_per_rep=args.seconds_per_rep
        )
    if args.synthetic:
        return synthesize(args.synthetic, fps=args.fps)
    if args.replay:
        return replay(args.replay, close=args.close, band=args.band, hold=args.hold)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
