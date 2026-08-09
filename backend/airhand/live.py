"""Live telemetry source — camera capture plus hand tracking on a worker thread.

OpenCV reads and MediaPipe inference both block, so they run off the asyncio loop. The WebSocket
server never waits on them: it samples `latest()` on its own schedule, which is what keeps a slow
or absent client from ever stalling the pipeline.
"""

from __future__ import annotations

import logging
import threading
import time

import cv2

from .calibration import CalibrationResult, CalibrationRunner, Observation
from .camera import CameraService, CameraUnavailable
from .cursor import DEFAULT_HOTKEY, CursorState, KillSwitch, build_cursor_engine
from .filters import LandmarkFilter
from .gestures import GestureEngine
from .pipeline import Sample, SourceStatus
from .pointer import PointerTracker
from .preview import PreviewEncoder
from .protocol import landmark_count
from .settings import DEFAULTS, EngineSettings
from .tracking import TrackingEngine

log = logging.getLogger(__name__)

# Smoothing factor for the FPS estimate. Low enough to be readable, high enough to show hitches.
FPS_SMOOTHING = 0.15

# How often to log the timing breakdown.
REPORT_INTERVAL_SECONDS = 5.0


class LiveSource:
    """Runs camera -> tracking on a thread and publishes the most recent Sample."""

    def __init__(
        self,
        *,
        camera_index: int = 0,
        min_detection_confidence: float = 0.5,
        width: int = 640,
        height: int = 480,
        camera_fps: float = 60.0,
        prefer_mjpg: bool = True,
        backend: str | None = None,
        settings: EngineSettings | None = None,
        cursor_dry_run: bool = False,
        killswitch_hotkey: str = DEFAULT_HOTKEY,
    ) -> None:
        self._camera_index = camera_index
        self._min_detection_confidence = min_detection_confidence
        self._width = width
        self._height = height
        self._camera_fps = camera_fps
        self._prefer_mjpg = prefer_mjpg
        self._backend = backend
        self._settings = settings or DEFAULTS
        # Set when settings change mid-run; the pipeline thread picks it up at the top of its next
        # iteration. Handing the running engines new config from another thread would race.
        self._pending_settings: EngineSettings | None = None

        # Built up front rather than inside the pipeline thread: the server thread needs to
        # enable/disable it, and the kill-switch listener needs to reach it from a third thread.
        self._cursor, self._cursor_state = build_cursor_engine(
            coverage=self._settings.cursor.coverage,
            center=(self._settings.cursor.center_x, self._settings.cursor.center_y),
            dry_run=cursor_dry_run,
        )
        self._killswitch: KillSwitch | None = None
        if self._cursor is not None:
            self._killswitch = KillSwitch(self._on_killswitch, hotkey=killswitch_hotkey)
            if not self._killswitch.start():
                # Refusing actuation is the right failure: arming the cursor with no way to stop
                # it is precisely the situation the kill-switch exists to prevent.
                self._cursor = None
                self._cursor_state = CursorState(
                    available=False,
                    enabled=False,
                    reason=(
                        "Cursor control is disabled because the emergency hotkey could not be "
                        "registered. Arming it with no way to stop it would be unsafe."
                    ),
                )
                self._killswitch = None

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._lock = threading.Lock()
        self._sample: Sample | None = None
        self._status = SourceStatus(camera="off", message="Not started")
        self._frame_index = 0

        # Opt-in and off to start with. Encoding runs on the pipeline thread — the same one that
        # drives the cursor — so a preview nobody asked for would spend that budget on nothing.
        self._preview = PreviewEncoder()

        # Idle unless the Calibration screen asks for a measurement. Observing is a list append,
        # so an idle runner costs the frame budget nothing.
        self._calibration = CalibrationRunner()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._set_status(SourceStatus(camera="starting", message="Opening camera"))
        self._thread = threading.Thread(target=self._run, name="airhand-pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # Disable actuation before the thread winds down, so a drag in progress cannot outlive
        # the pipeline that was driving it.
        if self._cursor is not None:
            self._cursor.set_enabled(False)

        # A session waits for frames this thread is about to stop producing, so it would otherwise
        # sit at "sampling" forever with a countdown that never moves.
        self._calibration.cancel()

        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3.0)
        self._thread = None
        with self._lock:
            self._sample = None
        # A stale preview outliving the camera would show a frozen frame that looks live.
        self._preview.clear()
        self._set_status(SourceStatus(camera="off", message="Stopped"))

    def status(self) -> SourceStatus:
        with self._lock:
            return self._status

    def cursor_state(self) -> CursorState:
        if self._cursor is None:
            return self._cursor_state
        return self._cursor.state()

    def set_cursor_enabled(self, enabled: bool) -> CursorState:
        if self._cursor is None:
            return self._cursor_state
        return self._cursor.set_enabled(enabled)

    @property
    def killswitch_hotkey(self) -> str | None:
        return self._killswitch.hotkey if self._killswitch else None

    def _on_killswitch(self) -> None:
        if self._cursor is not None:
            self._cursor.set_enabled(False)

    def latest(self) -> Sample | None:
        with self._lock:
            return self._sample

    def apply_settings(self, settings: EngineSettings) -> None:
        """Adopt new settings without restarting the pipeline.

        Called from the server thread. The gesture and pointer engines live on the pipeline thread,
        so they are not touched here — the change is parked and collected at the top of the next
        iteration. Coverage is different: the Cursor Engine is already thread-safe by design,
        because the kill-switch reaches it from a third thread.
        """
        with self._lock:
            self._settings = settings
            self._pending_settings = settings

        if self._cursor is not None:
            self._cursor.set_mapping(
                coverage=settings.cursor.coverage,
                center_x=settings.cursor.center_x,
                center_y=settings.cursor.center_y,
            )

    def start_calibration(self, step: str) -> CalibrationResult:
        with self._lock:
            settings = self._settings
        return self._calibration.start(step, settings=settings, now=time.monotonic())

    def cancel_calibration(self) -> None:
        self._calibration.cancel()

    def calibration(self) -> CalibrationResult | None:
        return self._calibration.result()

    def _take_pending_settings(self) -> EngineSettings | None:
        with self._lock:
            pending, self._pending_settings = self._pending_settings, None
        return pending

    def set_preview_enabled(self, enabled: bool) -> None:
        if self._preview.set_enabled(enabled):
            log.info("Preview stream %s", "enabled" if enabled else "disabled")

    def latest_preview(self) -> tuple[int, bytes] | None:
        return self._preview.latest()

    def _set_status(self, status: SourceStatus) -> None:
        with self._lock:
            self._status = status

    def _run(self) -> None:
        camera = CameraService(
            index=self._camera_index,
            width=self._width,
            height=self._height,
            fps=self._camera_fps,
            prefer_mjpg=self._prefer_mjpg,
            backend=self._backend,
        )
        engine = TrackingEngine(min_detection_confidence=self._min_detection_confidence)

        try:
            info = camera.open()
        except CameraUnavailable as exc:
            log.error("Camera unavailable: %s", exc)
            self._set_status(SourceStatus(camera="error", message=str(exc)))
            return

        try:
            engine.open()
        except (FileNotFoundError, RuntimeError) as exc:
            log.error("Tracking engine failed to start: %s", exc)
            camera.close()
            self._set_status(SourceStatus(camera="error", message=str(exc)))
            return

        self._set_status(
            SourceStatus(
                camera="on",
                camera_name=info.name,
                frame_width=info.width,
                frame_height=info.height,
            )
        )
        log.info("Pipeline running on %s", info.name)

        # Frame width / height. Everything the Gesture Engine measures is corrected by this, so a
        # 4:3 and a 16:9 camera produce the same numbers for the same gesture.
        aspect = (info.width / info.height) if info.height else 1.0

        landmark_filter = LandmarkFilter(count=landmark_count())
        gestures = GestureEngine(config=self._settings.gesture)
        # Runs in parallel with the landmark filter, on the same raw landmarks, because the two
        # want opposite things: gestures need low lag so a pinch is not a late click, pointing
        # needs heavy smoothing because the mapping amplifies its noise. See pointer.py.
        pointer = PointerTracker(config=self._settings.pointer)

        fps = 0.0
        previous_frame_at: float | None = None
        started_at = time.monotonic()
        last_report_at = started_at

        try:
            while not self._stop_event.is_set():
                # Collected here rather than assigned from the server thread: both engines are
                # owned by this loop, and a slider being dragged must not mutate them mid-frame.
                pending = self._take_pending_settings()
                if pending is not None:
                    gestures.config = pending.gesture
                    pointer.configure(pending.pointer)

                read_started = time.monotonic()
                frame = camera.read()
                capture_ms = (time.monotonic() - read_started) * 1000.0
                if frame is None:
                    self._set_status(
                        SourceStatus(
                            camera="error",
                            camera_name=info.name,
                            message="Camera stopped delivering frames. Was it unplugged?",
                        )
                    )
                    break

                capture_at = time.monotonic()

                # MediaPipe expects RGB; OpenCV delivers BGR.
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                timestamp_ms = int((capture_at - started_at) * 1000)

                inference_started = time.monotonic()
                result = engine.detect(frame_rgb, timestamp_ms)
                inference_ms = (time.monotonic() - inference_started) * 1000.0

                # Processing latency: frame in hand -> landmarks ready. Excludes sensor exposure,
                # driver buffering, transport and render, so it is a floor for end-to-end.
                # Time spent *waiting* for the frame is capture_ms and is throughput, not latency.
                latency_ms = (time.monotonic() - capture_at) * 1000.0

                dt = 0.0 if previous_frame_at is None else capture_at - previous_frame_at
                if previous_frame_at is not None and dt > 0:
                    instant = 1.0 / dt
                    fps = instant if fps == 0.0 else fps + FPS_SMOOTHING * (instant - fps)
                previous_frame_at = capture_at

                # Smooth before classifying. Raw landmark jitter produces phantom pinch
                # transitions that the engine's hysteresis should not have to absorb — and the
                # same smoothed points go to the UI, so the overlay stops trembling too.
                landmarks = result.landmarks
                if landmarks is not None and dt > 0:
                    landmarks = landmark_filter.filter(landmarks, dt)
                elif landmarks is None:
                    landmark_filter.reset()

                update = gestures.update(landmarks, aspect=aspect, now=capture_at)
                gesture, gesture_debug = update.gesture, update.debug

                self._frame_index += 1

                # Fed the *raw* landmarks, not the smoothed ones — the pointer owns its own
                # filter. Called on every frame, including empty ones, because it has dropout
                # bookkeeping to do; it returns None the moment the hand is gone, so a drag can
                # never outlive the hand that started it.
                anchor = pointer.update(
                    result.landmarks, hold=update.pointer_hold, now=capture_at
                )
                cursor = (
                    {"x": round(anchor[0], 4), "y": round(anchor[1], 4)} if anchor else None
                )

                # Fed every frame, including the empty ones — the lost-hand timer inside a
                # measurement runs on exactly those. No-op unless a session is running.
                self._calibration.observe(
                    Observation(
                        anchor=anchor,
                        pinch_index=gesture_debug.pinch_index if gesture_debug else None,
                        detected=result.detected,
                    ),
                    now=capture_at,
                )

                if self._cursor is not None:
                    # Events, never the sampled gesture: that field latches clicks for display,
                    # and a latch acted on every frame would fire a click per frame.
                    self._cursor.handle(update, anchor=anchor, frame_aspect=aspect)

                sample = Sample(
                    fps=fps,
                    latency_ms=latency_ms,
                    capture_ms=capture_ms,
                    inference_ms=inference_ms,
                    hand_detected=result.detected,
                    handedness=result.handedness,
                    gesture=gesture,
                    landmarks=landmarks,
                    cursor=cursor,
                    frame_index=self._frame_index,
                    gesture_debug=gesture_debug.to_message() if gesture_debug else None,
                )

                with self._lock:
                    self._sample = sample

                # Deliberately last in the loop body. The sample is published and the cursor has
                # already acted, so preview encoding can only push out the *next* frame — it can
                # never sit between a gesture and the click it produces.
                if self._preview.should_encode(capture_at):
                    self._preview.encode(frame, capture_at)

                # Periodic breakdown so the bottleneck is visible in the log without attaching a
                # client. If capture_ms dominates, the camera is the limit — not the model.
                now = time.monotonic()
                if now - last_report_at >= REPORT_INTERVAL_SECONDS:
                    last_report_at = now
                    pinch = (
                        f" | pinch idx {gesture_debug.pinch_index:.2f}"
                        f" mid {gesture_debug.pinch_middle:.2f}"
                        if gesture_debug
                        else ""
                    )
                    log.info(
                        "%.1f fps | capture %.1f ms | inference %.1f ms | latency %.1f ms"
                        " | %s%s",
                        fps,
                        capture_ms,
                        inference_ms,
                        latency_ms,
                        gesture,
                        pinch,
                    )
        except Exception:  # noqa: BLE001 - a thread that dies silently is worse
            log.exception("Pipeline thread crashed")
            self._set_status(
                SourceStatus(
                    camera="error",
                    camera_name=info.name,
                    message="Pipeline crashed — see engine logs.",
                )
            )
        finally:
            # Last line of defence: whatever went wrong above, the machine must not be left with
            # a mouse button held down.
            if self._cursor is not None:
                self._cursor.release_all("pipeline stopped")
            # Same idea, one level down: a measurement waiting on frames from a thread that has
            # just ended would show a countdown that never moves. This covers the camera-error
            # and crash paths, which never reach `stop()`.
            self._calibration.cancel()
            engine.close()
            camera.close()
            log.info("Pipeline stopped")
