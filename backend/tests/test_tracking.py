"""Camera Service and Tracking Engine.

These run without a webcam. Detection *quality* cannot be tested here — that needs a real hand in
front of a real camera — so what is covered is wiring, contracts and failure handling:

  - the vendored model exists and loads
  - a frame with no hand in it reports no hand (rather than crashing or inventing landmarks)
  - landmark output matches the protocol's shape and normalization
  - a missing camera degrades to a named error state instead of hanging or throwing
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from airhand.camera import CameraService, CameraUnavailable, discover_cameras
from airhand.live import LiveSource
from airhand.pipeline import Sample, SourceStatus, TelemetrySource
from airhand.protocol import landmark_count
from airhand.telemetry import SyntheticSource
from airhand.tracking import MODEL_VARIANT, TrackingEngine, model_path
from airhand.tracking.engine import handedness_label


# --------------------------------------------------------------------- model


def test_vendored_model_is_present() -> None:
    """FR-011: first run must not need a download."""
    path = model_path()
    assert path.is_file(), f"hand landmark model missing at {path}"
    assert path.stat().st_size > 1_000_000, "model file looks truncated"
    # Not a preference — MediaPipe Tasks publishes one Hand Landmarker bundle and documents it as
    # "full". The lite/full choice belonged to the legacy Solutions API. Pinned so that a future
    # session does not "upgrade" to a variant that does not exist, and because saved calibration
    # profiles will carry this string.
    assert MODEL_VARIANT == "full", "MediaPipe Tasks ships a single Hand Landmarker bundle"


# ------------------------------------------------------------------ tracking


@pytest.fixture(scope="module")
def engine():
    engine = TrackingEngine()
    engine.open()
    yield engine
    engine.close()


def test_blank_frame_reports_no_hand(engine: TrackingEngine) -> None:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    result = engine.detect(frame, timestamp_ms=0)
    assert result.detected is False
    assert result.landmarks is None
    assert result.handedness is None


def test_the_hand_label_is_reported_as_the_model_gave_it() -> None:
    """**Do not add a flip here.** It has been done once and it was wrong.

    The engine used to swap Left for Right, justified in a comment by the preview being mirrored
    for the user. That reasoning does not hold: mirroring a picture does not change which physical
    hand is in it. A left hand is a left hand in every representation of the image.

    The only reason anyone ever swaps this output is the **legacy Solutions API**, which documented
    handedness as assuming a mirrored selfie image. MediaPipe **Tasks** makes no such assumption.
    That is the second time a Solutions-era assumption reached this file — `model_complexity` and
    the "lite" variant were the first — so this test exists to stop a third.

    Found 2026-08-09: a user's left hand read "Right" on the Dashboard, confirmed against 1622
    recorded frames whose chirality was 100% consistent.
    """
    assert handedness_label("Left") == "left"
    assert handedness_label("Right") == "right"


def test_an_unlabelled_hand_stays_unlabelled_rather_than_guessing() -> None:
    """Landmarks can arrive without a classification. "Unknown" has to survive to the UI."""
    assert handedness_label(None) is None


def test_an_unexpected_label_is_passed_through_rather_than_dropped() -> None:
    """A category name this code has not seen is still information; losing it silently is worse."""
    assert handedness_label("Unknown") == "unknown"


def test_noise_frame_does_not_crash(engine: TrackingEngine) -> None:
    rng = np.random.default_rng(1234)
    frame = rng.integers(0, 255, size=(480, 640, 3), dtype=np.uint8)
    result = engine.detect(frame, timestamp_ms=100)
    # Whether noise trips a detection is not something to assert on; not crashing is.
    assert isinstance(result.detected, bool)
    if result.detected:
        assert result.landmarks is not None
        assert len(result.landmarks) == landmark_count()


def test_rejects_non_monotonic_timestamps_gracefully(engine: TrackingEngine) -> None:
    """MediaPipe's VIDEO mode throws on out-of-order timestamps; the engine must absorb that."""
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    engine.detect(frame, timestamp_ms=5_000)
    # Deliberately going backwards must not raise.
    result = engine.detect(frame, timestamp_ms=10)
    assert result.detected is False


def test_detect_before_open_is_an_error() -> None:
    unopened = TrackingEngine()
    with pytest.raises(RuntimeError):
        unopened.detect(np.zeros((10, 10, 3), dtype=np.uint8), timestamp_ms=0)


# -------------------------------------------------------------------- camera


def test_discover_cameras_returns_a_list_without_raising() -> None:
    """Must degrade quietly when no webcam is attached — the state on this machine."""
    cameras = discover_cameras(max_index=2)
    assert isinstance(cameras, list)
    for camera in cameras:
        assert camera.width > 0 and camera.height > 0


def test_opening_an_absent_camera_raises_camera_unavailable() -> None:
    # Index 99 will not exist on any realistic machine.
    service = CameraService(index=99)
    with pytest.raises(CameraUnavailable):
        service.open()
    service.close()


def test_live_source_reports_error_state_when_camera_is_absent() -> None:
    """The failure must surface as a named status, not a crash or a silent hang.

    systemPatterns.md requires "backend down", "camera off" and "no hand detected" to stay
    distinguishable; this is the camera branch of that.
    """
    source = LiveSource(camera_index=99)
    source.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            status = source.status()
            if status.camera == "error":
                break
            time.sleep(0.05)
        else:
            pytest.fail("LiveSource never reported an error for a missing camera")

        assert status.message
        assert source.latest() is None
    finally:
        source.stop()


# ------------------------------------------------------------------ contract


@pytest.mark.parametrize("source", [SyntheticSource(), LiveSource(camera_index=99)])
def test_sources_satisfy_the_telemetry_protocol(source: object) -> None:
    """Both sources are interchangeable from the server's point of view."""
    assert isinstance(source, TelemetrySource)


def test_synthetic_source_yields_protocol_shaped_samples() -> None:
    source = SyntheticSource(target_fps=60.0, seed=7)
    assert source.latest() is None, "a stopped source must produce nothing"

    source.start()
    assert source.status().camera == "on"

    sample = source.latest()
    assert isinstance(sample, Sample)
    if sample.hand_detected:
        assert sample.landmarks is not None
        assert len(sample.landmarks) == landmark_count()
        assert all(0.0 <= point[0] <= 1.0 and 0.0 <= point[1] <= 1.0 for point in sample.landmarks)

    source.stop()
    assert source.latest() is None
    assert source.status() == SourceStatus(camera="off", message="Synthetic source stopped")


def test_the_synthetic_source_labels_the_hand_it_actually_draws() -> None:
    """Derived from the landmarks, never asserted as a constant.

    A hardcoded expectation here would be the same mistake the engine made — a label agreeing with
    an assumption rather than with the geometry. `handmodel` places the thumb clearly to one side of
    the pinky, so the picture itself says which hand it is, and the reported label has to match it.

    The synthetic source got this wrong too: it announced "right" for geometry with the same
    chirality as a real left hand, so anyone tuning against `--source synthetic` was being taught
    the inversion a second time.
    """
    source = SyntheticSource(target_fps=60.0, seed=7)
    source.start()
    try:
        # Sampled at an explicit point in the script rather than polled: `latest()` reads the wall
        # clock, the first 1.2 s of the cycle is a fist with no reported hand, and a test that
        # slept its way past that would be both slow and dependent on scheduling. 2.0 s lands in
        # the "move" step, where a hand is definitely drawn.
        sample = source._sample(2.0)
        assert sample.hand_detected and sample.landmarks is not None, "no hand at this point"

        # MediaPipe's own landmark numbering: 2 is the thumb knuckle, 17 the pinky knuckle.
        thumb_x = sample.landmarks[2][0]
        pinky_x = sample.landmarks[17][0]
        assert thumb_x != pinky_x, "a hand with no width tells us nothing"

        # In a raw, unmirrored frame the thumb falls on the far side of the palm from the body,
        # so a thumb to the *left* of the pinky is the chirality of a left hand.
        drawn = "left" if thumb_x < pinky_x else "right"
        assert sample.handedness == drawn
    finally:
        source.stop()


def test_frame_index_advances_so_duplicates_are_detectable() -> None:
    source = SyntheticSource(seed=1)
    source.start()
    first = source.latest()
    second = source.latest()
    assert first is not None and second is not None
    assert second.frame_index > first.frame_index
    source.stop()
