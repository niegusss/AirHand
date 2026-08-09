"""AirHand CV engine entry point.

Runs standalone — the desktop app is one client of this process, not its owner. If
``python -m airhand.main`` stops working on its own, the engine/UI separation has been broken.

Implemented: Camera Service, Tracking Engine (MediaPipe, 21 landmarks), WebSocket transport with
handshake discovery and token auth.
Not implemented: Gesture Engine, Motion Filter, Cursor Engine — nothing here moves the OS cursor.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

from . import protocol
from .communication.server import EngineServer
from .cursor import DEFAULT_HOTKEY
from .handshake import default_handshake_path, generate_token, remove_handshake, write_handshake
from .pipeline import TelemetrySource
from .profile import LoadedProfile, default_profile_path, load_profile
from .settings import DEFAULTS, EngineSettings, InvalidSettings, merge

log = logging.getLogger("airhand")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airhand",
        description="AirHand Mouse computer-vision engine.",
    )
    parser.add_argument(
        "--source",
        choices=("camera", "synthetic"),
        default="camera",
        help=(
            "Telemetry source. 'camera' runs the real pipeline; 'synthetic' generates a fake "
            "stream so UI work does not need a webcam attached."
        ),
    )
    parser.add_argument("--camera-index", type=int, default=0, help="Camera index to open.")
    parser.add_argument("--camera-width", type=int, default=640, help="Requested frame width.")
    parser.add_argument("--camera-height", type=int, default=480, help="Requested frame height.")
    parser.add_argument(
        "--camera-fps",
        type=float,
        default=60.0,
        help="Requested camera frame rate. Advisory — drivers often grant less.",
    )
    parser.add_argument(
        "--camera-backend",
        choices=("auto", "msmf", "dshow"),
        default=None,
        help=(
            "Force a capture backend instead of trying them in order. Backends disagree about "
            "which frame rates a device supports, so this is a diagnostic worth having."
        ),
    )
    parser.add_argument(
        "--no-mjpg",
        action="store_true",
        help=(
            "Do not request MJPG. Uncompressed formats are bandwidth-limited over USB and "
            "usually cap the frame rate well below the sensor's capability — use only to compare."
        ),
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="Print the cameras that can be opened, then exit.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help=(
            "TCP port to bind. Default 0 = ephemeral, which is the production path. "
            "Pin a port only for browser development, where the UI cannot read the handshake file."
        ),
    )
    parser.add_argument(
        "--token",
        default=None,
        help=(
            "Auth token clients must present. Default: a fresh random token per launch. "
            "Pin one only for browser development (must match VITE_AIRHAND_WS_TOKEN)."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address. Loopback only.")
    parser.add_argument(
        "--fps",
        type=float,
        default=60.0,
        help="Rate at which the server polls the source for new frames.",
    )
    parser.add_argument(
        "--handshake",
        type=Path,
        default=None,
        help="Override the handshake file path.",
    )
    parser.add_argument(
        "--cursor-dry-run",
        action="store_true",
        help=(
            "Log cursor actions instead of performing them. Exercises the whole actuation path — "
            "mapping, clicks, drag bookkeeping — with no risk of a stray click."
        ),
    )
    # The tuning flags default to None rather than to a number, so "was it passed?" is answerable.
    # An explicit flag has to beat the saved profile: a flag that silently does nothing because a
    # profile exists is an hour of debugging waiting to happen.
    parser.add_argument(
        "--cursor-coverage",
        type=float,
        default=None,
        help=(
            "Fraction of frame width used as the active area, i.e. sensitivity. "
            "Smaller means less hand movement per screen pixel. "
            f"Overrides the saved profile for this run. Default {DEFAULTS.cursor.coverage}."
        ),
    )
    parser.add_argument(
        "--pinch-close",
        type=float,
        default=None,
        help=(
            "Thumb-to-index distance, in multiples of hand scale, at which a pinch counts as "
            "closed. Higher makes clicks easier to trigger. A hand that is not pinching measures "
            f"about 0.85, so that is the ceiling. Default {DEFAULTS.gesture.pinch_close}."
        ),
    )
    parser.add_argument(
        "--pinch-open",
        type=float,
        default=None,
        help=(
            "Distance at which the pinch counts as released. The gap to --pinch-close is the "
            "hysteresis band; without one, a hand resting near the threshold fires a burst of "
            f"clicks. Default {DEFAULTS.gesture.pinch_open}."
        ),
    )
    parser.add_argument(
        "--pointer-min-cutoff",
        type=float,
        default=None,
        help=(
            "Cursor smoothing at rest, in Hz. Lower is steadier but laggier. Tuned separately "
            "from the landmark filter because pointing and gesture detection want opposite "
            f"things. Default {DEFAULTS.pointer.min_cutoff}."
        ),
    )
    parser.add_argument(
        "--pointer-beta",
        type=float,
        default=None,
        help=(
            "How fast cursor smoothing gives way as the hand speeds up. Higher cuts lag during "
            "fast motion at the cost of letting more jitter through. "
            f"Default {DEFAULTS.pointer.beta}."
        ),
    )
    parser.add_argument(
        "--no-pointer-hold",
        action="store_true",
        help=(
            "Let the cursor follow the hand while a pinch is closing. On by default the cursor "
            "holds still, so a click lands where you aimed rather than where the hand drifted."
        ),
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=None,
        help=f"Override the calibration profile path. Default {default_profile_path()}.",
    )
    parser.add_argument(
        "--no-profile",
        action="store_true",
        help=(
            "Ignore the saved calibration profile and do not write one. Benchmarks and "
            "reproducible runs need settings that do not depend on the state of this machine."
        ),
    )
    parser.add_argument(
        "--killswitch",
        default=DEFAULT_HOTKEY,
        help="Global hotkey that disables cursor actuation, in pynput syntax.",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug logging.")
    return parser


def _resolve_settings(args: argparse.Namespace) -> tuple[EngineSettings, LoadedProfile | None]:
    """Settle the three sources of truth: explicit flag > saved profile > built-in default.

    Returns the settings to run with, and the profile the server should persist into — None when
    `--no-profile` says this run must neither read nor write one.
    """
    if args.no_profile:
        profile = None
        settings = DEFAULTS
    else:
        profile = load_profile(args.profile)
        settings = profile.settings
        if profile.reason:
            log.warning("%s", profile.reason)

    overrides: dict[str, dict[str, float | bool]] = {}
    if args.cursor_coverage is not None:
        overrides["cursor"] = {"coverage": args.cursor_coverage}
    gesture: dict[str, float | bool] = {}
    if args.pinch_close is not None:
        gesture["pinchClose"] = args.pinch_close
    if args.pinch_open is not None:
        gesture["pinchOpen"] = args.pinch_open
    if gesture:
        overrides["gesture"] = gesture
    pointer: dict[str, float | bool] = {}
    if args.pointer_min_cutoff is not None:
        pointer["minCutoff"] = args.pointer_min_cutoff
    if args.pointer_beta is not None:
        pointer["beta"] = args.pointer_beta
    if args.no_pointer_hold:
        pointer["holdOnPinch"] = False
    if pointer:
        overrides["pointer"] = pointer

    if overrides:
        # Validated through the same path as a patch off the wire, so a bad flag is refused with
        # the same message a bad slider would produce — and refused loudly, at startup.
        settings = merge(settings, overrides)
        log.info("Command-line settings override the saved profile for this run")

    return settings, profile


def _build_source(args: argparse.Namespace, settings: EngineSettings) -> TelemetrySource:
    if args.source == "synthetic":
        from .telemetry import SyntheticSource

        return SyntheticSource(target_fps=args.fps, settings=settings)

    # Imported lazily so `--source synthetic` works even where OpenCV/MediaPipe are unavailable.
    from .live import LiveSource

    return LiveSource(
        camera_index=args.camera_index,
        width=args.camera_width,
        height=args.camera_height,
        camera_fps=args.camera_fps,
        prefer_mjpg=not args.no_mjpg,
        backend=args.camera_backend,
        settings=settings,
        cursor_dry_run=args.cursor_dry_run,
        killswitch_hotkey=args.killswitch,
    )


def _list_cameras() -> int:
    from .camera import discover_cameras

    cameras = discover_cameras()
    if not cameras:
        print("No cameras found.")
        print(
            "On Windows this usually means the webcam is unplugged — OpenCV reports the backend "
            "as available but cannot capture by index when no device is attached."
        )
        return 1

    for camera in cameras:
        print(f"  [{camera.index}] {camera.name}  {camera.width}x{camera.height}")
    return 0


async def run(args: argparse.Namespace) -> int:
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        # The engine synthesizes OS input. Binding it to a routable interface would expose that
        # to the network, so refuse rather than trusting the caller.
        log.error("Refusing to bind to %s — the engine is loopback-only.", args.host)
        return 2

    token = args.token or generate_token()
    handshake_path: Path = args.handshake or default_handshake_path()

    try:
        settings, profile = _resolve_settings(args)
    except InvalidSettings as exc:
        log.error("Refusing to start: %s", exc)
        return 2

    source = _build_source(args, settings)
    source.start()

    server = EngineServer(
        source=source,
        token=token,
        host=args.host,
        port=args.port,
        target_fps=args.fps,
        settings=settings,
        profile=profile,
    )
    await server.start()
    port = server.port

    write_handshake(
        handshake_path,
        port=port,
        token=token,
        protocol_version=protocol.protocol_version(),
    )

    log.info("AirHand engine v%s", protocol.ENGINE_VERSION)
    log.info("Protocol v%s", protocol.protocol_version())
    log.info("Source: %s", args.source)
    log.info("Listening on ws://%s:%d", args.host, port)
    log.info("Handshake: %s", handshake_path)
    if profile is None:
        log.info("Calibration profile: disabled (--no-profile)")
    else:
        log.info(
            "Calibration profile: %s (%s)",
            profile.path,
            "loaded" if profile.loaded else "defaults",
        )
    if args.token:
        log.info("Token pinned via --token (development mode)")

    cursor = source.cursor_state()
    if cursor.available:
        log.info(
            "Cursor actuation available (%dx%d%s) — DISABLED until a client enables it. "
            "Emergency stop: %s",
            cursor.screen_width,
            cursor.screen_height,
            ", dry run" if cursor.dry_run else "",
            args.killswitch,
        )
    else:
        log.info("Cursor actuation unavailable: %s", cursor.reason)

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    serve_task = asyncio.create_task(server.serve_forever())
    stop_task = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (serve_task, stop_task):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(serve_task, stop_task, return_exceptions=True)
        await server.close()
        await asyncio.to_thread(source.stop)
        remove_handshake(handshake_path)
        log.info("Engine stopped; handshake removed")

    return 0


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows ProactorEventLoop does not support add_signal_handler; KeyboardInterrupt
            # from the default SIGINT handler covers the interactive case.
            signal.signal(sig, lambda *_: stop.set())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_cameras:
        return _list_cameras()

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
