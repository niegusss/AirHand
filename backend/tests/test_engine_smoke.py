"""End-to-end smoke test for the WebSocket engine.

Covers the parts the architecture decisions rest on: ephemeral port binding, atomic handshake
publication, token auth, protocol version agreement, and telemetry shape.

Runs against the synthetic source so it needs no webcam — the same reason the source exists.
Tests drive asyncio via ``asyncio.run`` directly so no pytest-asyncio plugin config is required.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from airhand import protocol
from airhand.communication.server import EngineServer
from airhand.cursor import CursorState
from airhand.handshake import remove_handshake, write_handshake
from airhand.profile import load_profile, save_profile
from airhand.settings import DEFAULTS, merge
from airhand.telemetry import SyntheticSource

TOKEN = "test-token-not-a-secret"


def _server(**kwargs) -> EngineServer:
    source = SyntheticSource(target_fps=kwargs.pop("target_fps", 120.0))
    source.start()
    return EngineServer(source=source, token=TOKEN, port=0, **kwargs)


async def _serve(server: EngineServer):
    await server.start()
    return asyncio.create_task(server.serve_forever())


def _run(coro):
    return asyncio.run(coro)


def test_binds_ephemeral_port_when_port_is_zero() -> None:
    async def scenario() -> int:
        server = _server()
        task = await _serve(server)
        try:
            return server.port
        finally:
            task.cancel()
            await server.close()

    port = _run(scenario())
    assert port > 0, "requesting port 0 must yield a real bound ephemeral port"


def test_handshake_write_is_atomic_and_complete(tmp_path: Path) -> None:
    target = tmp_path / "runtime.json"
    write_handshake(target, port=51873, token=TOKEN, protocol_version="1.0.0")

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["port"] == 51873
    assert payload["token"] == TOKEN
    assert payload["protocolVersion"] == "1.0.0"
    assert payload["pid"] == os.getpid()
    assert payload["startedAt"].endswith("Z")

    # No temp files may survive a successful write — a reader scanning the directory should
    # only ever find the finished handshake.
    assert [p.name for p in tmp_path.iterdir()] == ["runtime.json"]

    remove_handshake(target)
    assert not target.exists()
    remove_handshake(target)  # removing a missing handshake must not raise


def test_hello_precedes_auth_and_reports_shared_protocol_version() -> None:
    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                return json.loads(await client.recv())
        finally:
            task.cancel()
            await server.close()

    hello = _run(scenario())
    assert hello["type"] == "hello"
    # The version must come from shared/protocol/protocol.json, not a literal in the engine.
    assert hello["protocolVersion"] == protocol.protocol_version()


def test_rejects_bad_token() -> None:
    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await client.recv()  # hello
                await client.send(json.dumps({"type": "auth", "token": "wrong"}))
                return json.loads(await client.recv())
        finally:
            task.cancel()
            await server.close()

    message = _run(scenario())
    assert message["type"] == "error"
    assert message["code"] == "unauthorized"


def test_malformed_first_message_does_not_authenticate() -> None:
    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await client.recv()  # hello
                await client.send("this is not json")
                return json.loads(await client.recv())
        finally:
            task.cancel()
            await server.close()

    message = _run(scenario())
    assert message["type"] == "error"
    assert message["code"] == "unauthorized"


def test_authenticated_client_receives_status_then_telemetry() -> None:
    async def scenario() -> tuple[dict, dict]:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await client.recv()  # hello
                await client.send(json.dumps({"type": "auth", "token": TOKEN}))
                status = json.loads(await client.recv())

                telemetry = None
                for _ in range(20):
                    message = json.loads(await asyncio.wait_for(client.recv(), timeout=2.0))
                    if message["type"] == "telemetry":
                        telemetry = message
                        break
                assert telemetry is not None, "no telemetry arrived within 20 messages"
                return status, telemetry
        finally:
            task.cancel()
            await server.close()

    status, telemetry = _run(scenario())

    assert status["type"] == "status"
    assert status["tracking"] == "running"

    # Protocol 1.5.0. Landmarks are normalized against these, so a client without them can only
    # guess the frame's shape — and guessing is exactly what "runs on any webcam" rules out.
    assert status["frameWidth"] > 0
    assert status["frameHeight"] > 0

    assert telemetry["fps"] > 0
    assert telemetry["latencyMs"] > 0

    # Protocol 1.1.0 timing breakdown — this is what makes "why is FPS low" answerable.
    assert telemetry["captureMs"] >= 0
    assert telemetry["inferenceMs"] > 0
    assert telemetry["inferenceMs"] <= telemetry["latencyMs"], (
        "inference is a component of latency, so it cannot exceed it"
    )
    assert telemetry["gesture"] in (
        "none",
        "move",
        "left_click",
        "right_click",
        "drag",
        "scroll",
    )
    if telemetry["handDetected"]:
        assert len(telemetry["landmarks"]) == protocol.landmark_count()
        for point in telemetry["landmarks"]:
            assert len(point) == 3
            assert 0.0 <= point[0] <= 1.0
            assert 0.0 <= point[1] <= 1.0


def test_stop_command_halts_telemetry() -> None:
    async def scenario() -> list[str]:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await client.recv()  # hello
                await client.send(json.dumps({"type": "auth", "token": TOKEN}))
                await client.recv()  # status
                await client.send(json.dumps({"type": "command", "action": "stop"}))

                # Drain until the stop takes effect, then confirm the stream is quiet.
                deadline = asyncio.get_running_loop().time() + 2.0
                while asyncio.get_running_loop().time() < deadline:
                    message = json.loads(await client.recv())
                    if message["type"] == "status" and message["tracking"] == "idle":
                        break
                else:
                    pytest.fail("engine never reported tracking=idle")

                seen: list[str] = []
                try:
                    while True:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.4)
                        seen.append(json.loads(raw)["type"])
                except asyncio.TimeoutError:
                    pass
                return seen
        finally:
            task.cancel()
            await server.close()

    seen = _run(scenario())
    assert "telemetry" not in seen, "telemetry must stop when tracking is stopped"


# --------------------------------------------------------------------- settings

# Protocol 1.6.0. The channel the Calibration screen is built on: the engine owns the values, the
# client proposes changes, and the engine's answer is the only thing anyone renders.


async def _authenticate(client) -> None:
    await client.recv()  # hello
    await client.send(json.dumps({"type": "auth", "token": TOKEN}))


async def _await_type(client, kind: str, *, limit: int = 30) -> dict:
    for _ in range(limit):
        message = json.loads(await asyncio.wait_for(client.recv(), timeout=2.0))
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind!r} message within {limit} messages")


def test_settings_arrive_on_connect_with_bounds_and_defaults() -> None:
    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                return await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    message = _run(scenario())

    assert message["cursor"]["coverage"] > 0
    # Bounds and defaults ride along so the Calibration screen never keeps its own copy of the
    # ranges — a second copy is a copy that eventually disagrees with the engine.
    assert message["bounds"]["cursor"]["coverage"] == [0.2, 1.0]
    assert message["defaults"]["gesture"]["pinchClose"] > 0


def test_a_settings_change_reaches_every_client() -> None:
    """Two windows must not disagree about the engine's state — same rule as `cursorEnabled`."""

    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as writer, connect(
                f"ws://127.0.0.1:{server.port}"
            ) as reader:
                await _authenticate(writer)
                await _await_type(writer, "settings")
                await _authenticate(reader)
                await _await_type(reader, "settings")

                await writer.send(
                    json.dumps({"type": "set_settings", "cursor": {"coverage": 0.85}})
                )
                # The client that did not ask is the one worth checking.
                return await _await_type(reader, "settings")
        finally:
            task.cancel()
            await server.close()

    assert _run(scenario())["cursor"]["coverage"] == pytest.approx(0.85)


def test_an_invalid_patch_is_refused_and_changes_nothing() -> None:
    async def scenario() -> tuple[dict, dict]:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                before = await _await_type(client, "settings")

                await client.send(
                    json.dumps({"type": "set_settings", "cursor": {"coverage": 0.01}})
                )
                error = await _await_type(client, "error")
                return before, error
        finally:
            task.cancel()
            await server.close()

    before, error = _run(scenario())
    assert error["code"] == "invalid_settings"
    assert error["message"], "a refusal the user cannot read is not a refusal"
    # Nothing was broadcast, so the engine still holds what it held.
    assert before["cursor"]["coverage"] == pytest.approx(0.7)


def test_settings_survive_a_restart_without_any_client(tmp_path: Path) -> None:
    """The point of keeping the profile in the engine.

    `python -m airhand.main` has to stay standalone, so a calibration set from the UI has to be in
    force on a launch the UI was never part of.
    """
    profile_path = tmp_path / "profile.json"

    async def scenario() -> None:
        server = _server(profile=load_profile(profile_path))
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await _await_type(client, "settings")
                await client.send(
                    json.dumps({"type": "set_settings", "pointer": {"beta": 4.5}})
                )
                await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    _run(scenario())

    assert profile_path.is_file(), "a client change must reach the disk"
    assert load_profile(profile_path).settings.pointer.beta == pytest.approx(4.5)


def test_the_wire_reports_what_is_running_not_what_is_stored(tmp_path: Path) -> None:
    """A command-line flag overrides the profile for one run without rewriting it.

    Regression: the server first derived its settings from `profile.settings`, which looked
    equivalent and was not. The source ran the override while the wire reported the profile, so the
    Calibration screen would have shown a value the engine was not using — and the unit test on the
    CLI resolver could not see it, because the mistake was in the wiring above it.
    """
    profile_path = tmp_path / "profile.json"
    save_profile(merge(DEFAULTS, {"cursor": {"coverage": 0.83}}), profile_path)
    profile = load_profile(profile_path)
    override = merge(profile.settings, {"cursor": {"coverage": 0.5}})

    async def scenario() -> dict:
        server = _server(settings=override, profile=profile)
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                return await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    assert _run(scenario())["cursor"]["coverage"] == pytest.approx(0.5)
    # ...and the stored profile is untouched by a run that merely overrode it.
    assert load_profile(profile_path).settings.cursor.coverage == pytest.approx(0.83)


def test_a_server_without_a_profile_never_writes_one() -> None:
    """No profile means no persistence. A server nobody configured must not touch the machine."""

    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await _await_type(client, "settings")
                await client.send(
                    json.dumps({"type": "set_settings", "pointer": {"beta": 4.5}})
                )
                return await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    message = _run(scenario())
    assert message["pointer"]["beta"] == pytest.approx(4.5), "still applied for this session"
    assert message["profile"] is None


# ------------------------------------------------------------------------ calibration
#
# The derivations themselves are covered in `test_calibration.py`, against synthetic observations.
# What is tested here is the conversation: what the engine refuses, what it broadcasts, and the one
# piece of state the wizard leaves behind.


class _ScreenedSource(SyntheticSource):
    """Synthetic telemetry that also claims a screen, so the active area can be computed.

    The plain synthetic source reports no screen at all — it never actuates — and the active area
    is null without one.
    """

    def cursor_state(self) -> CursorState:
        return CursorState(available=True, enabled=False, screen_width=2560, screen_height=1080)


def test_calibrating_without_a_running_pipeline_is_refused() -> None:
    """A measurement with no frames behind it would report a countdown and then nothing."""

    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await _await_type(client, "settings")
                await client.send(json.dumps({"type": "command", "action": "stop"}))
                await _await_type(client, "status")

                await client.send(
                    json.dumps({"type": "calibrate", "action": "start", "step": "pinch"})
                )
                return await _await_type(client, "error")
        finally:
            task.cancel()
            await server.close()

    assert "tracking" in _run(scenario())["message"].lower()


def test_an_unknown_calibration_step_is_refused_rather_than_guessed() -> None:
    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await _await_type(client, "settings")
                await client.send(
                    json.dumps({"type": "calibrate", "action": "start", "step": "elbow"})
                )
                return await _await_type(client, "error")
        finally:
            task.cancel()
            await server.close()

    assert "elbow" in _run(scenario())["message"]


def test_a_measurement_reports_its_progress_to_every_client() -> None:
    """Same rule as settings: two windows must not disagree about where the wizard is."""

    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as writer, connect(
                f"ws://127.0.0.1:{server.port}"
            ) as reader:
                await _authenticate(writer)
                await _await_type(writer, "settings")
                await _authenticate(reader)
                await _await_type(reader, "settings")

                await writer.send(
                    json.dumps({"type": "calibrate", "action": "start", "step": "neutral"})
                )
                return await _await_type(reader, "calibration")
        finally:
            task.cancel()
            await server.close()

    message = _run(scenario())
    assert message["step"] == "neutral"
    assert message["state"] == "sampling"
    assert message["secondsRemaining"] > 0
    assert message["suggestion"] is None


def test_completing_the_wizard_is_what_marks_the_profile_calibrated(tmp_path: Path) -> None:
    """`loaded` cannot stand in for this: every settings change writes the file, so a profile
    exists the moment anyone nudges a slider."""
    profile_path = tmp_path / "profile.json"

    async def scenario() -> tuple[dict, dict]:
        server = _server(profile=load_profile(profile_path))
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                before = await _await_type(client, "settings")

                await client.send(json.dumps({"type": "calibrate", "action": "complete"}))
                return before, await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    before, after = _run(scenario())
    assert before["profile"]["calibrated"] is False
    assert after["profile"]["calibrated"] is True
    assert load_profile(profile_path).calibrated is True


def test_the_save_time_on_the_wire_is_the_one_in_the_file(tmp_path: Path) -> None:
    """One event, one timestamp.

    The `settings` message builds its profile block by hand, beside `LoadedProfile.to_message()`
    rather than through it — a duplication that already swallowed this field once. This asserts the
    two agree exactly, so the next divergence fails here instead of showing the user a date their
    file does not contain.
    """
    profile_path = tmp_path / "profile.json"

    async def scenario() -> tuple[dict, dict]:
        server = _server(profile=load_profile(profile_path))
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                fresh = await _await_type(client, "settings")

                await client.send(json.dumps({"type": "set_settings", "pointer": {"beta": 4.5}}))
                return fresh, await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    fresh, saved = _run(scenario())
    # Nothing had been written yet, so there is no date to report and none is invented.
    assert fresh["profile"]["savedAt"] is None
    assert saved["profile"]["savedAt"] == load_profile(profile_path).saved_at
    assert saved["profile"]["savedAt"] is not None


def test_an_ordinary_settings_change_does_not_undo_the_calibrated_mark(tmp_path: Path) -> None:
    """The trap in writing the whole profile on every change: the flag has to be carried across."""
    profile_path = tmp_path / "profile.json"

    async def scenario() -> dict:
        server = _server(profile=load_profile(profile_path))
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await _await_type(client, "settings")

                await client.send(json.dumps({"type": "calibrate", "action": "complete"}))
                await _await_type(client, "settings")
                await client.send(
                    json.dumps({"type": "set_settings", "pointer": {"beta": 4.5}})
                )
                return await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    assert _run(scenario())["profile"]["calibrated"] is True
    stored = load_profile(profile_path)
    assert stored.calibrated is True
    assert stored.settings.pointer.beta == pytest.approx(4.5)


def test_the_active_area_travels_so_the_client_never_does_the_mapping() -> None:
    """A copy of `active_area_for` in TypeScript would be CV logic in the frontend."""

    async def scenario() -> dict:
        source = _ScreenedSource(target_fps=120.0)
        source.start()
        server = EngineServer(source=source, token=TOKEN, port=0)
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await _await_type(client, "settings")
                await client.send(
                    json.dumps({"type": "set_settings", "cursor": {"centerX": 0.35}})
                )
                return await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    area = _run(scenario())["activeArea"]
    assert area is not None
    assert area["width"] == pytest.approx(DEFAULTS.cursor.coverage)
    # Follows the centre the client just set, which is the whole point of sending it.
    assert area["left"] + area["width"] / 2 == pytest.approx(0.35, abs=1e-3)


def test_the_active_area_is_null_when_there_is_no_screen_to_map_onto() -> None:
    """Honest null rather than a guess: no screen means actuation is unavailable entirely."""

    async def scenario() -> dict:
        server = _server()
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                return await _await_type(client, "settings")
        finally:
            task.cancel()
            await server.close()

    assert _run(scenario())["activeArea"] is None


def test_settings_reach_the_source() -> None:
    """Applied, not merely echoed. The synthetic source runs the real Gesture Engine."""

    async def scenario() -> float:
        source = SyntheticSource(target_fps=120.0)
        source.start()
        server = EngineServer(source=source, token=TOKEN, port=0)
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await _await_type(client, "settings")
                await client.send(
                    json.dumps({"type": "set_settings", "gesture": {"pinchClose": 0.22}})
                )
                await _await_type(client, "settings")
                return source._engine.config.pinch_close  # noqa: SLF001 - the point of the test
        finally:
            task.cancel()
            await server.close()

    assert _run(scenario()) == pytest.approx(0.22)


def test_capabilities_advertise_the_settings_channel() -> None:
    assert "settings" in protocol.hello()["capabilities"]


def test_server_does_not_resend_an_unchanged_frame() -> None:
    """The source may be slower than the poll rate; duplicates would inflate the apparent FPS."""

    async def scenario() -> list[int]:
        source = SyntheticSource(target_fps=120.0)
        source.start()
        server = EngineServer(source=source, token=TOKEN, port=0, target_fps=240.0)
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await client.recv()  # hello
                await client.send(json.dumps({"type": "auth", "token": TOKEN}))
                await client.recv()  # status

                timestamps: list[int] = []
                while len(timestamps) < 15:
                    message = json.loads(await asyncio.wait_for(client.recv(), timeout=2.0))
                    if message["type"] == "telemetry":
                        timestamps.append(message["ts"])
                return timestamps
        finally:
            task.cancel()
            await server.close()

    timestamps = _run(scenario())
    assert len(timestamps) == 15
