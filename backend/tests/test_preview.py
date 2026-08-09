"""Video preview stream — encoding, opt-in, and teardown.

The preview exists to be a blurred background in the UI, which is why every test here is really
asking the same question: *can this ever cost the cursor a frame?* The answer has to stay no, so
the rules under test are opt-in by default, downscale before encode, and stop the moment the last
viewer goes away.

No webcam required. Encoding is tested against a synthetic image; the transport is tested against
a fake source, the same split the rest of the suite uses.
"""

from __future__ import annotations

import asyncio
import json

import cv2
import numpy as np
from websockets.asyncio.client import connect

from airhand import protocol
from airhand.communication.server import EngineServer
from airhand.preview import PreviewEncoder
from airhand.telemetry import SYNTHETIC_ASPECT, SYNTHETIC_FRAME, SyntheticSource

TOKEN = "test-token-not-a-secret"


def _frame(width: int = 640, height: int = 480) -> np.ndarray:
    """A BGR frame with structure in it — a flat colour would compress to nothing."""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:, :, 1] = np.linspace(0, 255, width, dtype=np.uint8)
    frame[height // 3 : 2 * height // 3, width // 3 : 2 * width // 3] = 200
    return frame


class _PreviewSource(SyntheticSource):
    """Synthetic telemetry plus a preview stream, so transport can be tested without a camera."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.preview_enabled = False
        self._index = 0

    def set_preview_enabled(self, enabled: bool) -> None:
        self.preview_enabled = enabled

    def latest_preview(self) -> tuple[int, bytes] | None:
        if not self.preview_enabled:
            return None
        self._index += 1
        return self._index, b"\xff\xd8\xff" + bytes([self._index % 256]) * 32


async def _serve(server: EngineServer):
    await server.start()
    return asyncio.create_task(server.serve_forever())


async def _authenticate(client) -> None:
    await client.recv()  # hello
    await client.send(json.dumps({"type": "auth", "token": TOKEN}))
    await client.recv()  # status


# --------------------------------------------------------------------------- encoding


def test_encode_downscales_to_the_protocol_width_and_keeps_aspect() -> None:
    encoder = PreviewEncoder()
    assert encoder.encode(_frame(640, 480), now=0.0)

    result = encoder.latest()
    assert result is not None
    _, payload = result

    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert width == protocol.preview_max_width()
    # 640x480 is 4:3, so 320 wide must come back 240 tall. An aspect that drifts here would put a
    # stretched background under a correctly-proportioned landmark overlay.
    assert height == 240


def test_encoded_frame_is_small_enough_to_be_free_on_loopback() -> None:
    encoder = PreviewEncoder()
    encoder.encode(_frame(), now=0.0)

    result = encoder.latest()
    assert result is not None
    _, payload = result

    # ~10 KB at 15 fps is ~150 KB/s. The point of the assertion is that a regression in quality
    # or resolution shows up here rather than as a mysterious FPS drop.
    assert len(payload) < 25_000, f"preview frame is {len(payload)} bytes — too big for a backdrop"


def test_a_frame_already_smaller_than_the_limit_is_not_upscaled() -> None:
    encoder = PreviewEncoder()
    encoder.encode(_frame(160, 120), now=0.0)

    result = encoder.latest()
    assert result is not None
    decoded = cv2.imdecode(np.frombuffer(result[1], dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (120, 160)


def test_index_advances_so_a_consumer_can_tell_frames_apart() -> None:
    encoder = PreviewEncoder()
    encoder.encode(_frame(), now=0.0)
    first = encoder.latest()
    encoder.encode(_frame(), now=1.0)
    second = encoder.latest()

    assert first is not None and second is not None
    assert second[0] > first[0]


# --------------------------------------------------------------------------- opt-in


def test_preview_is_off_until_asked_for() -> None:
    """The whole cost argument rests on this: nobody watching must cost nothing."""
    encoder = PreviewEncoder()
    assert encoder.enabled is False
    assert encoder.latest() is None
    assert encoder.should_encode(now=1e6) is False, "a disabled encoder is never due"


def test_disabling_drops_the_last_frame() -> None:
    encoder = PreviewEncoder()
    encoder.set_enabled(True)
    encoder.encode(_frame(), now=0.0)
    assert encoder.latest() is not None

    encoder.set_enabled(False)
    assert encoder.latest() is None, (
        "a preview served after the viewer left would show a frozen frame that looks live"
    )


def test_throttle_keeps_the_preview_well_under_the_camera_rate() -> None:
    """The cursor and the preview share a thread, so the preview gets a slice, not every frame.

    The guarantee is a ceiling, not an exact count: whether a given camera frame is due depends on
    where its timestamp falls relative to the interval, so the rate lands at or just below target.
    Undershooting is harmless for a blurred backdrop; overshooting would eat the cursor's budget.
    """
    encoder = PreviewEncoder(fps=15.0)
    encoder.set_enabled(True)

    encoded = 0
    # One second of a 30 fps camera, frame by frame.
    for step in range(30):
        now = step / 30.0
        if encoder.should_encode(now):
            encoder.encode(_frame(), now)
            encoded += 1

    assert encoded <= 15, f"encoded {encoded} frames — the throttle is not holding the ceiling"
    assert encoded >= 10, f"encoded only {encoded} frames — the throttle has collapsed the rate"


def test_preview_settings_come_from_the_shared_protocol_file() -> None:
    """Both halves read the same file, so the rate cannot drift between engine and UI."""
    encoder = PreviewEncoder()
    assert encoder.interval == 1.0 / protocol.preview_fps()
    assert encoder.max_width == protocol.preview_max_width()


def test_synthetic_source_never_pretends_to_have_a_camera() -> None:
    source = SyntheticSource()
    source.start()
    source.set_preview_enabled(True)
    assert source.latest_preview() is None


# --------------------------------------------------------------------------- transport


def test_preview_frames_arrive_as_binary_only_after_enable_preview() -> None:
    async def scenario() -> tuple[bool, bytes | None]:
        source = _PreviewSource(target_fps=120.0)
        source.start()
        server = EngineServer(source=source, token=TOKEN, port=0, target_fps=240.0)
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)

                # Before asking: text only.
                quiet = True
                try:
                    for _ in range(15):
                        raw = await asyncio.wait_for(client.recv(), timeout=1.0)
                        if isinstance(raw, bytes):
                            quiet = False
                            break
                except asyncio.TimeoutError:
                    pass

                await client.send(json.dumps({"type": "command", "action": "enable_preview"}))

                frame: bytes | None = None
                deadline = asyncio.get_running_loop().time() + 3.0
                while asyncio.get_running_loop().time() < deadline:
                    raw = await asyncio.wait_for(client.recv(), timeout=2.0)
                    if isinstance(raw, bytes):
                        frame = raw
                        break
                return quiet, frame
        finally:
            task.cancel()
            await server.close()

    quiet_before, frame = asyncio.run(scenario())
    assert quiet_before, "preview frames must not be sent before a client asks for them"
    assert frame is not None, "no binary preview frame arrived after enable_preview"
    assert frame.startswith(b"\xff\xd8\xff"), "a preview frame must be a JPEG"


def test_disable_preview_stops_the_stream_and_the_encoding() -> None:
    async def scenario() -> tuple[bool, bool]:
        source = _PreviewSource(target_fps=120.0)
        source.start()
        server = EngineServer(source=source, token=TOKEN, port=0, target_fps=240.0)
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await client.send(json.dumps({"type": "command", "action": "enable_preview"}))

                deadline = asyncio.get_running_loop().time() + 3.0
                while asyncio.get_running_loop().time() < deadline:
                    if isinstance(await asyncio.wait_for(client.recv(), timeout=2.0), bytes):
                        break
                else:  # pragma: no cover - only on a broken stream
                    raise AssertionError("preview never started")

                await client.send(json.dumps({"type": "command", "action": "disable_preview"}))
                await asyncio.sleep(0.3)  # let anything already in flight land

                # Telemetry keeps flowing, so "silence" is not a thing to wait for here — watch a
                # fixed window and require that none of what arrives is binary.
                stopped = True
                deadline = asyncio.get_running_loop().time() + 1.0
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        raw = await asyncio.wait_for(client.recv(), timeout=0.5)
                    except asyncio.TimeoutError:
                        break
                    if isinstance(raw, bytes):
                        stopped = False
                        break
                return stopped, source.preview_enabled
        finally:
            task.cancel()
            await server.close()

    stopped, still_encoding = asyncio.run(scenario())
    assert stopped, "binary frames kept arriving after disable_preview"
    assert not still_encoding, "the source must stop encoding, not just stop sending"


def test_disconnecting_stops_the_encoder() -> None:
    """Same dead-man reasoning as cursor actuation: a viewer that left must stop costing frames."""

    async def scenario() -> bool:
        source = _PreviewSource(target_fps=120.0)
        source.start()
        server = EngineServer(source=source, token=TOKEN, port=0, target_fps=240.0)
        task = await _serve(server)
        try:
            async with connect(f"ws://127.0.0.1:{server.port}") as client:
                await _authenticate(client)
                await client.send(json.dumps({"type": "command", "action": "enable_preview"}))
                deadline = asyncio.get_running_loop().time() + 3.0
                while asyncio.get_running_loop().time() < deadline:
                    if isinstance(await asyncio.wait_for(client.recv(), timeout=2.0), bytes):
                        break

            # Let the server's disconnect handling run.
            await asyncio.sleep(0.3)
            return source.preview_enabled
        finally:
            task.cancel()
            await server.close()

    assert asyncio.run(scenario()) is False


def test_engine_advertises_the_preview_capability() -> None:
    assert "preview" in protocol.hello()["capabilities"]


def test_synthetic_source_reports_its_own_frame_geometry() -> None:
    """Every source declares the shape its landmarks are normalized against — no exceptions.

    A source reporting nothing would force the UI back to guessing 4:3, which is what the overlay
    used to do and why the hand came out stretched.
    """
    source = SyntheticSource()
    source.start()
    status = source.status()

    assert status.frame_width == SYNTHETIC_FRAME
    assert status.frame_height == SYNTHETIC_FRAME
    # Square, matching SYNTHETIC_ASPECT — the geometry reported and the geometry the gesture
    # engine is told about have to be the same thing.
    assert status.frame_width / status.frame_height == SYNTHETIC_ASPECT


def test_frame_geometry_is_absent_before_the_camera_opens() -> None:
    source = SyntheticSource()
    status = source.status()
    assert status.frame_width is None and status.frame_height is None
