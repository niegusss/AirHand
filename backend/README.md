# AirHand engine

The Python computer-vision engine. Runs standalone — the desktop app is a client of this process,
not its owner.

## Status

**Implemented:** Camera Service, Tracking Engine (MediaPipe hand landmarker, 21 points), Motion
Filter (One Euro), Gesture Engine (all five gestures), Pointer stage, **Cursor Engine**, camera
preview stream, WebSocket transport with handshake discovery and token auth.

**This engine can move your mouse.** Actuation is off at every launch and must be enabled
explicitly by a client — it is never restored automatically, unlike tracking.

### Emergency stop

`Ctrl+Alt+Space` disables actuation instantly. It is a global hotkey, so it works regardless of
window focus and regardless of whether the pointer is usable — which is the point: you cannot
click a "stop" button with a cursor that is misbehaving. Change it with `--killswitch`.

If the hotkey cannot be registered, the engine refuses to offer actuation at all rather than
arming it with no way out.

Use `--cursor-dry-run` to exercise the whole actuation path with actions logged instead of
performed.

**Windows UIPI:** a non-elevated process cannot inject input into windows running as
administrator. A click that "does nothing" over an elevated app is this, not a bug.

### Pointing

`airhand/pointer.py` decides where the cursor should be. It is separate from both neighbours on
purpose:

- **Not in `cursor/`**, because that package is the only code that writes to the OS and stays as
  small as its safety rules allow. The pointer touches nothing, so it can be tested directly.
- **Not in `filters/`**, because the landmark filter serves the Gesture Engine and the UI overlay,
  which want *low* lag — a late pinch is a late click. Pointing wants the opposite: the mapping
  multiplies normalized coordinates by roughly 2700 px at the default coverage, so detector noise
  that is invisible in the overlay is several pixels of tremor on screen.

The two filters therefore run **in parallel on the same raw landmarks**, never in series — chaining
them would add their lag together for no benefit. Three things live here:

| | |
|---|---|
| Palm-centroid anchor | The cursor follows the mean of the five rigid palm landmarks (wrist + four knuckles), not one of them. Averaging attenuates the independent part of the detector's noise at zero latency cost, because it is stateless. |
| Hold during an undecided pinch | From the moment a pinch closes until it resolves into a click or a drag, the cursor stops following the hand, so the click lands where you aimed. A drag then starts from the point that was clicked. Disable with `--no-pointer-hold`. |
| Dropout grace | A brief detection loss keeps the smoothing state, so re-acquisition is not a burst of unsmoothed samples. **It never keeps the output** — no hand means no anchor, immediately, or a drag could outlive the hand that started it. |

Tune with `--pointer-min-cutoff` and `--pointer-beta` against `tools/bench_pointer.py`. The
Calibration screen owns both at runtime, along with `--cursor-coverage` and the active area's
centre; the flags remain for reproducible runs and benchmarks.

### Calibration measurement

`airhand/calibration.py` turns a few seconds of a real hand into settings. Three steps, driven from
the UI over `calibrate` (protocol 1.7.0) and measured on the pipeline thread, which is the only
place that sees every frame's anchor and pinch distance:

| Step | Duration | What it measures | What it suggests |
|---|---|---|---|
| `neutral` | 3 s | median resting anchor | `cursor.centerX` / `centerY` |
| `reach` | 6 s | p02–p98 spread from the centre | `cursor.coverage` |
| `pinch` | 8 s | the **worst** of several attempts | `gesture.pinchClose` / `pinchOpen` |

**A suggestion is a `set_settings` patch, verbatim.** Accepting a measurement is the ordinary
settings path, with the ordinary validation and the ordinary persistence — there is deliberately no
second way to write a setting.

**Nothing is a min, a max, or a mean.** Detection drops frames, an occluded thumb throws a single
wild reading, and a hand told to hold still drifts; every derivation uses a median or a percentile
so no one frame can define the result. The pinch threshold keys on the *worst* attempt, not the
deepest — a threshold tuned to the best pinch misses the others, which is the lost-click bug.

**The pinch margin is a fraction of the measured gap, not a fixed offset.** A deliberate pinch is
always a clean one; the clicks that go missing are the ones where an occluded thumb pushes the
estimate up, and the width of the gap is the only evidence about how much room that needs. A flat
offset put the threshold at 0.26 on a hand whose gap was 0.98 wide — see `progress.md`.

**Two refusals, both deliberate.** Fewer than two clear pinches means there is nothing to conclude
from. And a threshold that would land close to the resting hand is refused outright: an open hand
was measured at a 0.853 minimum on a real trace, and a phantom click is a worse failure than the
missed click this step exists to fix.

### Calibration profile

Tunable settings — gesture thresholds, pointer smoothing, cursor sensitivity — are defined once in
`airhand/settings.py`, which is also where their bounds and defaults live. The CLI, the wire format
and the validator all read that file, so none of them can disagree about what a legal value is.

Changes arrive over the WebSocket (`set_settings`, protocol 1.6.0), apply without restarting the
pipeline, and are saved to `%LOCALAPPDATA%\AirHand\profile.json`.

The file also carries `calibrated`, set only by `calibrate` / `complete`. It is **not** the same
question as "does a profile exist": every settings change writes this file, so a profile is there
the moment anyone nudges a slider. The desktop app's mandatory-first-run gate reads `calibrated`.

**The profile belongs to the engine, not the desktop app.** This process has to keep working
standalone, so a calibration set from the UI must be in force on a launch the UI was never part of.
It is a separate file from `runtime.json`: the handshake is deleted on clean shutdown, the profile
has to survive one.

Three sources of truth, in order: **explicit command-line flag > saved profile > built-in default.**
A flag overrides for one run without rewriting the profile. `--no-profile` ignores the disk
entirely, which is what benchmarks and reproducible runs need.

Every profile is stamped with the model it was calibrated against. On a mismatch it is **not
applied** — thresholds are expressed in terms of MediaPipe's landmark placement, so a profile
written under a different model is not evidence about this one. Defaults stand and the reason
travels to the UI.

Values are refused, never clamped. `coverage` has a floor of 0.2 rather than "greater than zero",
because a smaller active area produces a pointer too twitchy to click the window in which you would
undo the setting.

### Camera preview

The engine can stream the camera image as downscaled JPEG frames (binary WebSocket frames), which
the desktop UI renders blurred behind the interface. It is **opt-in per client** — nothing is
encoded until someone sends `enable_preview`, and encoding stops the moment the last subscriber
disconnects.

Encoding runs on the pipeline thread, the same one that drives the cursor, so it is downscaled to
320 px and throttled below the tracking rate. Measured on the dev camera: **no change to FPS or
latency** (30.4 fps / 11.5 ms with preview off and on), ~6 KB per frame at ~11 frames/s.

`PreviewEncoder` lives in `airhand/preview.py` and needs neither a camera nor a cursor, which is
why it can be tested without either.

Two interchangeable telemetry sources sit behind one interface (`pipeline.TelemetrySource`):

| `--source` | What it does |
|---|---|
| `camera` (default) | Real webcam + MediaPipe |
| `synthetic` | Generated stream, no webcam needed — for UI work. Produces no preview frames: there is no camera to preview, and rendering the scripted hand into a fake image would be a convincing lie. |

Swapping between them required no change to the server or the wire format, which was the point of
the split.

## Setup

Windows PowerShell 5.1 — no `&&`; chain with `;` and prefix local executables with `.\`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Commands below call `.\.venv\Scripts\python.exe` directly so they work whether or not the venv is
activated.

CV dependencies are listed separately in `requirements-cv.txt` and are **not installed yet** —
see that file before adding them.

## Run

```powershell
# Production shape: ephemeral port, random per-launch token, handshake file published
.\.venv\Scripts\python.exe -m airhand.main

# Browser development: pin both, because a browser cannot read the handshake file
.\.venv\Scripts\python.exe -m airhand.main --port 8765 --token dev-token

# No webcam attached? Run the UI against generated telemetry instead
.\.venv\Scripts\python.exe -m airhand.main --port 8765 --token dev-token --source synthetic

# Which cameras can actually be opened
.\.venv\Scripts\python.exe -m airhand.main --list-cameras

# Ignore the saved calibration profile — for benchmarks and reproducible runs
.\.venv\Scripts\python.exe -m airhand.main --no-profile
```

The pinned values must match `apps/desktop/.env.local`. Stop with `Ctrl+C` — a graceful shutdown
removes the handshake file, a hard kill does not.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
```

170 tests, no webcam required. They cover ephemeral port binding, atomic handshake writes, token
auth, protocol-version agreement, telemetry shape, `stop` halting the stream, duplicate-frame
suppression, model presence, blank/noise frame handling, out-of-order timestamps, the
missing-camera error path, gesture classification, hysteresis and dropout grace, cursor mapping and
actuation safety, the pointer stage's anchor, hold and dropout rules, the settings channel's
validation, persistence and flag precedence, and the preview stream's opt-in, throttling and
teardown.

Detection *quality* is not covered — that needs a real hand in front of a real camera. What the
suite proves is wiring, contracts and failure handling.

## Cameras

OpenCV has no device-list API, so `discover_cameras()` probes indices. Device **names** are not
available on Windows without a DirectShow enumeration dependency, so cameras are reported by
index — worth revisiting if you end up with several and cannot tell them apart.

If OpenCV logs *"backend is generally available but can't be used to capture by index"*, the
webcam is almost certainly unplugged rather than the backend being broken. Check with:

```powershell
Get-PnpDevice -Class Camera | Select-Object Status,FriendlyName
```

`Status: Unknown` means Windows remembers the device but it is not currently connected.

## Handshake

Published to `%LOCALAPPDATA%\AirHand\runtime.json`, written atomically and removed on clean
shutdown. See `shared/protocol/README.md` for the full contract.

**A hard kill leaves the file behind.** That is expected and unavoidable — there is no cleanup
opportunity. Readers must verify `pid` is alive before trusting a handshake, and re-spawn rather
than hanging on a dead port.

## Layout

```text
airhand/
├── main.py               # entry point, CLI, lifecycle, flag-over-profile resolution
├── protocol.py           # version + message builders (reads shared/protocol/protocol.json)
├── model.py              # which landmark model this is — no MediaPipe import, so anything can ask
├── settings.py           # tunable values, their bounds and defaults; validation and merging
├── profile.py            # calibration profile on disk, stamped with the model
├── jsonfile.py           # atomic JSON write + tolerant read, shared by handshake and profile
├── handshake.py          # atomic handshake publication
├── pipeline.py           # Sample + TelemetrySource — the seam the server sees
├── live.py               # real source: camera -> filter -> gestures, on a worker thread
├── telemetry.py          # synthetic source, same interface
├── handmodel.py          # synthetic hand geometry, shared with the tests
├── camera/
│   └── service.py        # device discovery, frame capture — every OpenCV call lives here
├── tracking/
│   └── engine.py         # MediaPipe HandLandmarker, 21 landmarks
├── preview.py            # camera preview encoder — opt-in, downscaled, throttled
├── pointer.py            # where the cursor should be: palm anchor, its own filter, click hold
├── filters/
│   └── one_euro.py       # adaptive smoothing; frame-rate independent by construction
├── gestures/
│   ├── features.py       # hand-scale + aspect normalization — camera independence lives here
│   └── engine.py         # FSM with hysteresis, time-based debounce, dropout grace
├── cursor/
│   ├── screen.py         # screen geometry (Windows, DPI-aware); primary monitor only
│   ├── mapping.py        # active area -> screen, pure functions
│   ├── backends.py       # pynput / dry-run / (tests supply a recording backend)
│   ├── killswitch.py     # global emergency hotkey
│   └── engine.py         # actuation + all the safety rules
└── communication/
    └── server.py         # WebSocket server; no CV logic
```

## Tools

```powershell
.\.venv\Scripts\python.exe tools\bench_camera.py     # raw camera throughput per backend/format
.\.venv\Scripts\python.exe tools\bench_gestures.py   # filter + classification cost per frame
.\.venv\Scripts\python.exe tools\bench_pointer.py    # cursor jitter and lag, in screen pixels
.\.venv\Scripts\python.exe tools\bench_pinch.py      # why a pinch did not become a click
```

`bench_pointer` and `bench_pinch` share one idea, in `tools/tracefile.py`: record a trace of **raw**
landmarks once, then replay it through as many configurations as you like. Every candidate sees
identical detector output on identical frame timings, so tuning does not mean waving at the webcam
between restarts — and the same recording that diagnoses a problem also proves the fix.

`bench_pinch` reports per attempt how close the fingers got, whether detection dropped out, and
what the classifier decided, with the dropout grace on and off side by side.

Both take `--synthetic`, which builds a trace with known properties. That is how the measurements
themselves get checked without a camera — and one of them was wrong the first time, twice.

**Recording needs the engine stopped.** OpenCV holds the webcam exclusively on Windows:
`Get-Process python | Stop-Process -Force`.

`bench_gestures` exists because the engine log's `inferenceMs` times **only** MediaPipe — filtering
and classification sit outside that timer, so a rise there can never be blamed on them. Measured at
61 µs/frame, or 0.18% of a 30 fps budget.

Blocking calls (OpenCV reads, MediaPipe inference) run on a worker thread in `live.py`, never on
the asyncio loop. The server samples `latest()` on its own schedule, so a slow or absent client
can never stall the pipeline.
