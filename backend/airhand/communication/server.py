"""WebSocket server — status, settings synchronization and telemetry.

Contains no computer-vision logic. It owns the wire format and the connection lifecycle only;
frames and landmarks come from whatever :class:`TelemetrySource` it was handed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Iterable

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .. import protocol
from ..calibration import CalibrationResult
from ..cursor.mapping import active_area_for
from ..pipeline import MAX_CAMERA_PROBE_INDEX, SourceStatus, TelemetrySource
from ..profile import LoadedProfile, now_stamp, save_profile
from ..settings import DEFAULTS, EngineSettings, InvalidSettings, merge, settings_message

log = logging.getLogger(__name__)

# How long a client has to present its token before we drop it.
AUTH_TIMEOUT_SECONDS = 5.0

# How often to re-check the source's status. Status changes are rare; this is not a hot path.
STATUS_POLL_SECONDS = 0.25

# How often to look for a new preview frame. Slightly faster than the source produces them, so
# polling is never the thing that limits the preview rate.
PREVIEW_POLL_SECONDS = 1.0 / 25.0


class EngineServer:
    """Serves telemetry to authenticated local clients."""

    def __init__(
        self,
        *,
        source: TelemetrySource,
        token: str,
        host: str = "127.0.0.1",
        port: int = 0,
        target_fps: float = 60.0,
        settings: EngineSettings | None = None,
        profile: LoadedProfile | None = None,
        camera_index: int | None = None,
    ) -> None:
        self._source = source
        self._token = token
        self._host = host
        self._requested_port = port
        self._target_fps = target_fps
        # The engine owns the settings, not the UI: `python -m airhand.main` has to keep working
        # standalone, and a profile living in the desktop app's storage would break that quietly.
        #
        # `settings` and `profile` are separate arguments and must stay that way. What is *running*
        # is not always what is *stored*: a command-line flag overrides the profile for one run
        # without rewriting it. Deriving these settings from `profile.settings` looked equivalent
        # and was not — the source ran the override while the wire reported the profile, so the
        # Calibration screen would have shown a number the engine was not using.
        #
        # No profile means no persistence. A server nobody handed a profile must not start writing
        # to the user's machine on its own — which also keeps every test that does not care about
        # persistence away from the real one.
        self._profile = profile
        self._settings = settings or (profile.settings if profile else DEFAULTS)
        self._profile_reason = profile.reason if profile else None
        self._saved = False
        """When the profile was last written. Starts as whatever the loaded file recorded, then
        tracks this session's own writes — the on-disk value moves and the UI has to move with it."""
        self._saved_at = profile.saved_at if profile else None
        # Whether the user has been through the wizard, as opposed to merely having a profile on
        # disk. Every settings change writes that file, so `loaded` says nothing about calibration.
        # Held here because `save_profile` is told the flag rather than merging it from disk.
        self._calibrated = profile.calibrated if profile else False
        self._clients: set[ServerConnection] = set()
        # Clients that asked for the video preview. Kept separate from `_clients` so encoding is
        # driven by who is actually watching, not by who happens to be connected.
        self._preview_clients: set[ServerConnection] = set()
        self._server: Server | None = None
        self._tracking: protocol.TrackingState = "running"
        self._last_status: SourceStatus | None = None
        self._last_frame_index = -1
        self._last_preview_index = -1
        self._last_calibration: CalibrationResult | None = None
        self._last_area: dict[str, float] | None = None
        # The user's *explicit* device choice, or None if they have never made one. Held here for
        # the same reason as `_calibrated`: every write to the profile is a full rewrite, so a value
        # that is not carried on each `_persist` is erased by the next unrelated settings change.
        #
        # Deliberately not read back off the source, which cannot distinguish a chosen index from
        # the fallback it happened to be constructed with.
        self._camera_index = camera_index
        # Last scan's result. Empty until someone asks — probing opens devices, so it is not
        # something to do on connect.
        self._devices: list[dict[str, Any]] = []
        self._scanning = False
        self._scan_reason: str | None = None
        # A scan stops and restarts the pipeline. Two of them interleaving would have one restart
        # the camera while the other was still probing it.
        self._scan_lock = asyncio.Lock()

    @property
    def port(self) -> int:
        """The actually bound port. Only valid once :meth:`start` has returned."""
        if self._server is None:
            raise RuntimeError("Server has not been started")
        sockets = getattr(self._server, "sockets", None) or []
        for sock in sockets:
            return int(sock.getsockname()[1])
        raise RuntimeError("Server has no bound socket")

    async def start(self) -> None:
        self._server = await serve(self._handle_client, self._host, self._requested_port)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("Server has not been started")
        pumps = [
            asyncio.create_task(self._telemetry_loop()),
            asyncio.create_task(self._status_loop()),
            asyncio.create_task(self._preview_loop()),
        ]
        try:
            await self._server.serve_forever()
        except asyncio.CancelledError:
            raise
        finally:
            for task in pumps:
                task.cancel()
            # Let them unwind before the loop closes, so cancellation does not surface as a
            # "task was destroyed but it is pending" warning during shutdown.
            await asyncio.gather(*pumps, return_exceptions=True)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    # ------------------------------------------------------------------ clients

    async def _handle_client(self, connection: ServerConnection) -> None:
        peer = connection.remote_address
        log.info("Client connected from %s", peer)

        try:
            await self._send(connection, protocol.hello())
            if not await self._authenticate(connection):
                return

            self._clients.add(connection)
            await self._send(connection, self._status_message())
            await self._send(connection, self._settings_message())
            # Sent unasked so a reconnecting client knows which device is selected without
            # triggering a scan — a scan opens hardware, and reconnects happen on their own.
            # The device *list* is whatever the last scan found, legitimately empty at first.
            await self._send(connection, self._cameras_message())
            await self._receive_loop(connection)
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(connection)
            # Same reasoning as the cursor dead-man switch, one level cheaper: a viewer that left
            # must stop costing the pipeline encode time.
            self._preview_clients.discard(connection)
            self._sync_preview_subscription()
            log.info("Client disconnected from %s", peer)
            if not self._clients:
                # Dead-man switch: the intent to actuate came from a client. If the UI crashed or
                # was closed, that intent is gone and the user has lost their off switch.
                await self._disable_cursor_after_disconnect()

    async def _authenticate(self, connection: ServerConnection) -> bool:
        """Require a valid token as the first message, within a short deadline."""
        try:
            raw = await asyncio.wait_for(connection.recv(), timeout=AUTH_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await self._send(connection, protocol.error("unauthorized", "No auth message received"))
            await connection.close(code=4401, reason="unauthorized")
            return False

        message = self._decode(raw)
        if message is None or message.get("type") != "auth":
            await self._send(
                connection, protocol.error("unauthorized", "First message must be 'auth'")
            )
            await connection.close(code=4401, reason="unauthorized")
            return False

        # Constant-time comparison is overkill on loopback, but the token is a secret and
        # comparing secrets with == is a habit worth not forming.
        if not _tokens_match(str(message.get("token", "")), self._token):
            await self._send(connection, protocol.error("unauthorized", "Invalid token"))
            await connection.close(code=4401, reason="unauthorized")
            return False

        return True

    async def _receive_loop(self, connection: ServerConnection) -> None:
        async for raw in connection:
            message = self._decode(raw)
            if message is None:
                # A malformed message must never be able to drive engine behavior.
                await self._send(connection, protocol.error("internal", "Malformed message"))
                continue

            kind = message.get("type")
            if kind == "ping":
                await self._send(connection, {"type": "pong", "ts": message.get("ts")})
            elif kind == "command":
                await self._handle_command(connection, str(message.get("action", "")))
            elif kind == "set_settings":
                await self._handle_set_settings(connection, message)
            elif kind == "select_camera":
                await self._handle_select_camera(connection, message)
            elif kind == "calibrate":
                await self._handle_calibrate(connection, message)
            else:
                await self._send(
                    connection, protocol.error("internal", f"Unknown message type: {kind!r}")
                )

    async def _handle_command(self, connection: ServerConnection, action: str) -> None:
        """start / pause / stop.

        `pause` keeps the camera open and merely stops emitting, so resuming is instant.
        `stop` releases the camera — that is what "stop tracking" has to mean once the Cursor
        Engine exists, and pretending otherwise would train the wrong expectation now.
        """
        if action == "start":
            if self._tracking != "paused":
                await asyncio.to_thread(self._source.start)
            self._tracking = "running"
        elif action == "pause":
            self._tracking = "paused"
            # The pipeline keeps running while paused, so actuation has to be stopped explicitly
            # or the cursor would carry on moving behind a UI that says "paused".
            await asyncio.to_thread(self._source.set_cursor_enabled, False)
        elif action == "stop":
            self._tracking = "idle"
            await asyncio.to_thread(self._source.stop)
        elif action == "enable_cursor":
            state = await asyncio.to_thread(self._source.set_cursor_enabled, True)
            if not state.enabled:
                await self._send(
                    connection,
                    protocol.error(
                        "internal",
                        state.reason or "Cursor actuation is unavailable on this machine.",
                    ),
                )
        elif action == "disable_cursor":
            await asyncio.to_thread(self._source.set_cursor_enabled, False)
        elif action == "enable_preview":
            self._preview_clients.add(connection)
            self._sync_preview_subscription()
            return  # Preview is not part of the status message; no broadcast to make.
        elif action == "disable_preview":
            self._preview_clients.discard(connection)
            self._sync_preview_subscription()
            return
        elif action == "discover_cameras":
            await self._handle_discover(connection)
            return  # It broadcasts its own status and cameras messages, in order.
        else:
            await self._send(connection, protocol.error("internal", f"Unknown action: {action!r}"))
            return

        log.info("Tracking state -> %s", self._tracking)
        await self._broadcast(self._status_message())

    async def _handle_set_settings(
        self, connection: ServerConnection, message: dict[str, Any]
    ) -> None:
        """Validate, apply, persist, broadcast — in that order, and all-or-nothing.

        A refused patch changes nothing at all. Applying its valid half would leave the engine in
        a state no client asked for, and the client would have no way to learn which half landed.
        """
        try:
            updated = merge(self._settings, message)
        except InvalidSettings as exc:
            # Not `internal`: this is the caller's problem and the message is meant to be shown.
            await self._send(connection, protocol.error("invalid_settings", str(exc)))
            return

        if updated == self._settings:
            return

        self._settings = updated
        await asyncio.to_thread(self._source.apply_settings, updated)
        await self._persist()

        log.info("Settings updated by client")
        await self._broadcast(self._settings_message())

    async def _handle_discover(self, connection: ServerConnection) -> None:
        """Scan for capture devices, stopping and resuming the pipeline around the probe.

        The pipeline has to come down first: on Windows an open device cannot be opened a second
        time, so probing mid-capture returns a list with the camera currently in use missing from
        it — a device that is plainly working appearing not to exist, with nothing on screen able
        to explain it.

        **`tracking` is reported as `idle` for the duration.** Leaving it at `running` over a
        released camera would be a lie the UI acts on: the client's own stall detection would fire
        a few hundred milliseconds in and report a broken pipeline in the middle of a healthy scan.
        """
        if self._scan_lock.locked():
            await self._send(
                connection, protocol.error("internal", "A camera scan is already running.")
            )
            return

        async with self._scan_lock:
            was_running = self._tracking == "running"
            self._scanning = True
            self._scan_reason = None
            await self._broadcast(self._cameras_message())

            if was_running:
                await asyncio.to_thread(self._source.stop)
                self._tracking = "idle"
                await self._broadcast(self._status_message())

            try:
                devices = await asyncio.to_thread(self._source.discover)
                self._devices = [device.to_message() for device in devices]
                self._scan_reason = None if devices else (
                    "No cameras found. Indices are probed from 0 to "
                    f"{MAX_CAMERA_PROBE_INDEX - 1}, so a device numbered above that is not seen — "
                    "and a webcam held by another application cannot be opened here either."
                )
            except Exception as exc:  # noqa: BLE001 - a failed scan must not drop the client
                log.exception("Camera scan failed")
                self._devices = []
                self._scan_reason = f"The camera scan failed: {exc}"
            finally:
                self._scanning = False
                # In `finally` on purpose. A scan that raised must still give the user back the
                # pipeline it took away — otherwise one bad probe leaves a working camera dark.
                if was_running:
                    await asyncio.to_thread(self._source.start)
                    self._tracking = "running"
                    await self._broadcast(self._status_message())

        log.info("Camera scan found %d device(s)", len(self._devices))
        await self._broadcast(self._cameras_message())

    async def _handle_select_camera(
        self, connection: ServerConnection, message: dict[str, Any]
    ) -> None:
        """Choose a capture device, and remember the choice.

        **Re-selecting the current device is not a no-op.** After a camera fails to open, picking
        it again is the natural way to ask for another attempt, and short-circuiting on equality
        would turn the one obvious recovery action into a button that does nothing.
        """
        index = message.get("index")
        # `bool` before `int`: True is an instance of int and would otherwise select camera 1.
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            await self._send(
                connection,
                protocol.error(
                    "invalid_settings",
                    f"Camera index must be a non-negative integer, got {index!r}.",
                ),
            )
            return

        self._camera_index = index
        # The source decides whether this restarts anything: running means reopen, stopped means
        # take effect on the next start. A device that cannot be opened surfaces through the
        # ordinary `camera: "error"` status rather than being rolled back to the previous one —
        # silently running a camera the user did not choose is the invisible kind of wrong.
        await asyncio.to_thread(self._source.set_camera, index)
        await self._persist()

        log.info("Camera index -> %d", index)
        await self._broadcast(self._cameras_message())
        await self._broadcast(self._status_message())

    def _cameras_message(self) -> dict[str, Any]:
        return protocol.cameras(
            devices=self._devices,
            selected=self._camera_index,
            scanning=self._scanning,
            reason=self._scan_reason,
        )

    async def _persist(self) -> None:
        """Write the profile, if there is one to write.

        A failed write does not undo the change. It is already live, and refusing a working
        adjustment because the disk is full would be the worse trade — the reason travels to the
        UI instead.
        """
        if self._profile is None:
            return
        # Generated here and handed to the writer, so the file and the `settings` broadcast carry
        # the identical string. Letting each read the clock would put them a second apart often
        # enough to be noticed, and they describe the same event.
        stamp = now_stamp()
        self._profile_reason = await asyncio.to_thread(
            save_profile,
            self._settings,
            self._profile.path,
            calibrated=self._calibrated,
            saved_at=stamp,
            # Carried on every write, exactly like `calibrated`. A save is a full rewrite, so
            # omitting it here would erase the user's camera choice on the next slider nudge —
            # nowhere near anything to do with cameras, and impossible to connect to a cause.
            camera_index=self._camera_index,
        )
        self._saved = True
        if self._profile_reason is None:
            self._saved_at = stamp

    def _settings_message(self) -> dict[str, Any]:
        area = self._active_area()
        self._last_area = area
        if self._profile is None:
            return settings_message(self._settings, active_area=area)
        return settings_message(
            self._settings,
            active_area=area,
            profile={
                "path": str(self._profile.path),
                # Once a client has saved, the settings are on disk whatever happened at startup.
                # Reporting the original load result forever would go stale the moment the user
                # calibrated for the first time.
                "loaded": self._profile.loaded or self._saved,
                "stale": self._profile.stale and not self._saved,
                "reason": self._profile_reason,
                "calibrated": self._calibrated,
                "savedAt": self._saved_at,
            },
        )

    def _active_area(self) -> dict[str, float] | None:
        """The rectangle the current settings actually produce, in normalized frame coordinates.

        Computed here — where the screen and the camera are both known — rather than in the UI.
        The arithmetic belongs to `cursor/mapping.py`, and a copy of it in TypeScript would put
        computer-vision logic in the frontend, which this project does not allow.

        None whenever either shape is unknown: no screen means actuation is unavailable, and no
        frame size means the camera has not opened yet.
        """
        cursor = self._source.cursor_state()
        status = self._source.status()
        if not (cursor.screen_width and cursor.screen_height):
            return None
        if not (status.frame_width and status.frame_height):
            return None

        area = active_area_for(
            screen_aspect=cursor.screen_width / cursor.screen_height,
            frame_aspect=status.frame_width / status.frame_height,
            coverage=self._settings.cursor.coverage,
            center=(self._settings.cursor.center_x, self._settings.cursor.center_y),
        )
        return {
            "left": round(area.left, 4),
            "top": round(area.top, 4),
            "width": round(area.width, 4),
            "height": round(area.height, 4),
        }

    async def _handle_calibrate(
        self, connection: ServerConnection, message: dict[str, Any]
    ) -> None:
        """Run one measurement step, or record that the wizard finished.

        The measurement itself belongs to the source: only it sees every frame's anchor and pinch
        distance. This end owns the conversation — refusing what cannot work, and broadcasting the
        outcome to every client so two windows cannot disagree about where the wizard is.
        """
        action = str(message.get("action", ""))

        if action == "start":
            if self._tracking != "running":
                await self._send(
                    connection,
                    protocol.error(
                        "internal",
                        "Start tracking before calibrating — a measurement needs live frames.",
                    ),
                )
                return

            # Actuation off for the duration. The user is about to sweep their hand across the
            # frame on purpose, and doing that while it drives the pointer would throw the cursor
            # off the window they are calibrating from.
            await asyncio.to_thread(self._source.set_cursor_enabled, False)

            try:
                result = await asyncio.to_thread(
                    self._source.start_calibration, str(message.get("step", ""))
                )
            except ValueError as exc:
                await self._send(connection, protocol.error("internal", str(exc)))
                return

            log.info("Calibration step %r started", result.step)
            self._last_calibration = result
            await self._broadcast(result.to_message())
            await self._broadcast(self._status_message())
            return

        if action == "cancel":
            await asyncio.to_thread(self._source.cancel_calibration)
            result = self._source.calibration()
            if result is not None:
                self._last_calibration = result
                await self._broadcast(result.to_message())
            return

        if action == "complete":
            # The only thing that sets this. A profile exists the moment any setting changes, so
            # without a separate marker "must calibrate on first run" would be satisfied by anyone
            # who nudged a slider.
            self._calibrated = True
            await self._persist()
            log.info("Calibration completed — profile marked calibrated")
            await self._broadcast(self._settings_message())
            return

        await self._send(
            connection, protocol.error("internal", f"Unknown calibrate action: {action!r}")
        )

    @property
    def settings(self) -> EngineSettings:
        """Current settings. Exposed for tests and for the CLI's startup log."""
        return self._settings

    def _sync_preview_subscription(self) -> None:
        """Tell the source whether anyone is watching.

        The source does the expensive part (downscale + JPEG), so this is what keeps the cost at
        exactly zero when the UI is closed or has never asked.
        """
        self._source.set_preview_enabled(bool(self._preview_clients))

    async def _disable_cursor_after_disconnect(self) -> None:
        state = await asyncio.to_thread(self._source.cursor_state)
        if not state.enabled:
            return
        log.warning("Last client disconnected — disabling cursor actuation")
        await asyncio.to_thread(self._source.set_cursor_enabled, False)

    # ------------------------------------------------------------------ pumps

    async def _telemetry_loop(self) -> None:
        interval = 1.0 / self._target_fps
        while True:
            await asyncio.sleep(interval)
            if not self._clients or self._tracking != "running":
                continue

            sample = self._source.latest()
            if sample is None:
                continue

            # The source may produce frames more slowly than we poll. Re-sending an unchanged
            # frame would inflate the apparent rate and hide real drops from the UI.
            if sample.frame_index == self._last_frame_index:
                continue
            self._last_frame_index = sample.frame_index

            await self._broadcast(sample.to_message(time.time()))

    async def _preview_loop(self) -> None:
        """Ship the newest preview frame to whoever asked for it.

        Two rules make this safe to run beside the cursor:

        - **Never re-send a frame.** The index guards it, exactly as `frame_index` does for
          telemetry — a repeat would inflate the apparent rate and waste loopback bandwidth.
        - **Drop, never queue.** `_send_many` is awaited before the next frame is even read, so a
          slow client cannot build a backlog. A dropped background frame costs nothing; a growing
          send queue costs latency and memory, and this is video.
        """
        while True:
            await asyncio.sleep(PREVIEW_POLL_SECONDS)
            if not self._preview_clients or self._tracking != "running":
                continue

            frame = self._source.latest_preview()
            if frame is None:
                continue

            index, payload = frame
            if index == self._last_preview_index:
                continue
            self._last_preview_index = index

            await self._send_many(tuple(self._preview_clients), payload)

    async def _status_loop(self) -> None:
        """Poll everything that changes without a client asking: status, measurement progress and
        the active area.

        One loop rather than three: all three are cheap reads of state the source already holds,
        and each extra task is another thing to cancel correctly on shutdown.
        """
        while True:
            await asyncio.sleep(STATUS_POLL_SECONDS)
            if not self._clients:
                continue

            status = self._source.status()
            if status != self._last_status:
                self._last_status = status
                await self._broadcast(self._status_message(status))

            # A measurement reports its own countdown, so this is what makes the wizard's progress
            # move. It stops changing the moment the session ends, so an idle engine is silent.
            calibration = self._source.calibration()
            if calibration is not None and calibration != self._last_calibration:
                self._last_calibration = calibration
                await self._broadcast(calibration.to_message())

            # The active area depends on the camera's shape as well as the settings, so opening a
            # camera changes it without any setting having moved. Without this the Calibration
            # screen would draw last frame-size's reach box.
            if self._active_area() != self._last_area:
                await self._broadcast(self._settings_message())

    def _status_message(self, status: SourceStatus | None = None) -> dict[str, Any]:
        resolved = status if status is not None else self._source.status()
        self._last_status = resolved
        cursor = self._source.cursor_state()
        return protocol.status(
            camera=resolved.camera,
            tracking=self._tracking,
            camera_name=resolved.camera_name,
            camera_index=resolved.camera_index,
            message=resolved.message,
            frame_width=resolved.frame_width,
            frame_height=resolved.frame_height,
            cursor_available=cursor.available,
            cursor_enabled=cursor.enabled,
            cursor_reason=cursor.reason,
            cursor_dry_run=cursor.dry_run,
            killswitch_hotkey=getattr(self._source, "killswitch_hotkey", None),
        )

    # ------------------------------------------------------------------ transport

    async def _broadcast(self, message: dict[str, Any]) -> None:
        if not self._clients:
            return
        payload = json.dumps(message, separators=(",", ":"))
        # Snapshot the set: a send failure mutates self._clients via the handler's finally block.
        await self._send_many(tuple(self._clients), payload)

    async def _send_many(
        self, targets: Iterable[ServerConnection], payload: str | bytes
    ) -> None:
        """Send one payload to many clients. `bytes` goes out as a binary frame — a preview JPEG."""
        results = await asyncio.gather(
            *(target.send(payload) for target in targets), return_exceptions=True
        )
        for target, result in zip(targets, results):
            if isinstance(result, Exception):
                # A slow or dead client must never stall the pipeline — drop it and move on.
                self._clients.discard(target)
                self._preview_clients.discard(target)

    async def _send(self, connection: ServerConnection, message: dict[str, Any]) -> None:
        await connection.send(json.dumps(message, separators=(",", ":")))

    @staticmethod
    def _decode(raw: str | bytes) -> dict[str, Any] | None:
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            parsed = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None


def _tokens_match(candidate: str, expected: str) -> bool:
    import hmac

    return hmac.compare_digest(candidate, expected)
