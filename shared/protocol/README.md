# AirHand WebSocket Protocol

The contract between the Python CV engine and the React desktop UI.
`protocol.json` holds the canonical version — **both sides read that file**, so the version cannot
drift. Do not hardcode the version anywhere else.

## Discovery

The engine binds `127.0.0.1:0` (ephemeral port) and publishes a handshake file:

- Windows: `%LOCALAPPDATA%\AirHand\runtime.json`

```json
{
  "pid": 12345,
  "port": 51873,
  "protocolVersion": "1.0.0",
  "token": "<random per launch>",
  "startedAt": "2026-08-08T10:15:00Z"
}
```

Written atomically (temp file + `os.replace`) so a reader never sees a partial file. Removed on
clean shutdown. A reader that finds a file whose `pid` is not alive must treat it as stale.

**Browsers cannot read this file.** In the packaged app, Tauri's Rust layer reads it and hands the
values to the webview. In browser development, use the `VITE_AIRHAND_WS_URL` /
`VITE_AIRHAND_WS_TOKEN` overrides and start the engine with matching `--port` / `--token` flags.

## Connection sequence

1. Client opens the WebSocket.
2. Server immediately sends `hello`.
3. Client checks `protocolVersion` against its own. Mismatch → disconnect with a named error;
   do not attempt to interoperate.
4. Client sends `auth` with the per-launch token.
5. Server either starts streaming `status` + `telemetry`, or sends `error` with `unauthorized`
   and closes.

The token exists because loopback is not an authorization boundary on a multi-user machine, and
this server drives real OS input.

## Server → client

### `hello`
```jsonc
{ "type": "hello", "protocolVersion": "1.7.0", "engineVersion": "0.1.0",
  "capabilities": ["telemetry", "preview", "settings"] }
```

### `status`
Low frequency — sent on change only.
```jsonc
{
  "type": "status",
  "camera": "off" | "starting" | "on" | "error",
  "tracking": "idle" | "running" | "paused",
  "cameraName": "string | null",
  "cpuPercent": 0.0,
  "message": "string | null",

  // Added in 1.5.0. Null until the camera is open.
  "frameWidth": 640,
  "frameHeight": 480,

  // Added in 1.3.0.
  "cursorAvailable": false,       // can this machine actuate at all?
  "cursorEnabled": false,         // is it actuating right now? authoritative — see below
  "cursorReason": "string | null",// why unavailable
  "cursorDryRun": false,          // actions are logged, not performed
  "killswitchHotkey": "<ctrl>+<alt>+<space>"
}
```

**`cursorEnabled` is authoritative, not an echo.** The engine disables actuation on its own — the
kill-switch, a pause, or the last client disconnecting — so a client that renders what it last
requested will show a control that lies. Render this field.

**`frameWidth` / `frameHeight` are what landmarks are normalized against.** MediaPipe divides `x`
by width and `y` by height, so drawing a landmark into a container of a different shape stretches
the hand along one axis. A client must shape its overlay from these, never assume 4:3 — there is
no reference webcam (`PM-QUESTIONS.md` #6), which is the whole reason they are on the wire.

Every source reports them, including the synthetic one (square, 480×480). A source that reports
nothing has not opened its camera yet.

### `settings`
Added in 1.6.0. Sent after `status` on connect, and re-broadcast to **every** client on change.

```jsonc
{
  "type": "settings",
  "gesture": { "pinchClose": 0.50, "pinchOpen": 0.70, "holdToDragSeconds": 0.4,
               "clickLatchSeconds": 0.2, "extendedAngleDegrees": 150.0, "scrollStep": 0.25,
               "dropoutGraceSeconds": 0.15 },
  "pointer": { "minCutoff": 0.8, "beta": 6.0, "dCutoff": 1.0,
               "holdOnPinch": true, "dropoutGraceSeconds": 0.2 },
  "cursor":  { "coverage": 0.7, "centerX": 0.5, "centerY": 0.5 },

  "bounds":   { "cursor": { "coverage": [0.2, 1.0] }, /* ...; null for booleans */ },
  "defaults": { /* same shape as the values above */ },

  // 1.7.0. The rectangle those cursor values actually produce, in normalized frame coordinates.
  // Null until both the screen and the camera are known.
  "activeArea": { "left": 0.15, "top": 0.24, "width": 0.7, "height": 0.52 },

  "profile": {                       // null when the engine runs with --no-profile
    "path": "%LOCALAPPDATA%\\AirHand\\profile.json",
    "loaded": true,
    "stale": false,                  // a profile exists but was refused
    "reason": null,
    "calibrated": false              // 1.7.0 — has the wizard been completed?
  }
}
```

**The engine is authoritative.** Render what arrived, never what was requested — same rule as
`cursorEnabled`, and for the same reason: a patch can be refused, and the engine can be running
values a client never sent (a command-line flag overrides the profile for one run).

**`bounds` and `defaults` ride along on every message** so no client keeps its own copy of the
ranges. A second copy is one that eventually disagrees, and the disagreement surfaces as a slider
offering a value the engine then refuses. Same reasoning as `gestureDebug.thresholds`.

**`profile.stale` means a saved profile was found and not applied** — most often because it was
calibrated against a different MediaPipe model, whose landmark placement every threshold here is
expressed in terms of. Show `reason` and offer a re-calibration; showing plain defaults would
imply nothing had ever been saved.

**`profile.calibrated` is not `profile.loaded`.** Every settings change writes the profile, so a
file exists the moment anyone nudges a slider. Only `calibrate` / `complete` sets `calibrated`, so
that is the field a "calibration is mandatory on first run" check must read.

**`activeArea` is sent so no client reproduces the mapping.** Deriving it needs `coverage`, the
centre, the screen aspect and the frame aspect, and gets the aspect handling wrong in a way that is
invisible until someone pairs an ultrawide with a 4:3 webcam. The engine owns that arithmetic
(`cursor/mapping.py`); a client draws the rectangle it is given.

### `calibration`
Added in 1.7.0. Broadcast when a measurement starts, while it is sampling (~4 Hz), and when it
reaches a verdict.

```jsonc
{
  "type": "calibration",
  "step": "pinch",                   // "neutral" | "reach" | "pinch"
  "state": "done",                   // "sampling" | "done" | "failed"
  "samples": 231,
  "secondsRemaining": 0.0,
  "secondsTotal": 8.0,               // so a progress bar needs no copy of the step durations
  "measurement": { "restingLevel": 0.94, "attempts": 3,
                   "worstPinch": 0.38, "bestPinch": 0.30 },
  "suggestion": { "gesture": { "pinchClose": 0.46, "pinchOpen": 0.66 } },
  "reason": null                     // why `failed`, meant to be shown to the user
}
```

**`suggestion` is a `set_settings` patch, verbatim.** Accepting a measurement is the ordinary
settings path with the ordinary validation and the ordinary persistence — there is deliberately no
second way to write a setting. It is null whenever the engine will not stand behind a number:
too few pinch attempts to conclude anything, or a threshold that would sit close enough to the
resting hand to fire on its own.

**`measurement` accompanies a `failed` verdict whenever there was one**, so the user can see what
was seen rather than only that it did not work. It is null when the session ended before any
derivation ran — the hand left the frame, or the pipeline stopped.

### `telemetry`
High frequency (~60 Hz). The client must throttle rendering — never render once per message.

On the timing fields, because they are easy to misread:

| Field | Meaning |
|---|---|
| `latencyMs` | Frame in hand → landmarks ready. Excludes sensor exposure, driver buffering, transport and render, so it is a **floor** for end-to-end latency, not the number itself. |
| `captureMs` | Time blocked waiting for the camera to deliver a frame. Explains **throughput, not latency** — the frame is fresh when it arrives, so the wait is idle time, not staleness. Low FPS with low `latencyMs` means the time went here. |
| `inferenceMs` | The model's share of `latencyMs`; the remainder is colour conversion. |

```jsonc
{
  "type": "telemetry",
  "ts": 1754647200.123,          // seconds, engine monotonic-derived epoch
  "fps": 59.4,
  "latencyMs": 18.2,
  "captureMs": 44.1,             // added in 1.1.0
  "inferenceMs": 9.6,            // added in 1.1.0
  "handDetected": true,
  "handedness": "left" | "right" | null,
  "gesture": "none" | "move" | "left_click" | "right_click" | "drag" | "scroll",
  "landmarks": [[x, y, z], ...],  // 21 triples, normalized 0..1; null when no hand
  "cursor": { "x": 0.51, "y": 0.32 }, // normalized; null when not tracking

  // Added in 1.2.0. Omitted entirely (not null) when there is no hand.
  "gestureDebug": {
    "state": "pinch_index_pending",  // internal FSM state, richer than `gesture`
    "handScale": 0.2143,             // reference length: wrist -> middle MCP, isotropic units
    "pinchIndex": 0.31,              // thumb tip -> index tip, in multiples of handScale
    "pinchMiddle": 0.88,             // thumb tip -> middle tip, same units
    "extended": { "thumb": false, "index": false, "middle": true, "ring": false, "pinky": false },
    "angles":   { "thumb": 141.2, "index": 88.4, "middle": 171.0, "ring": 96.1, "pinky": 92.7 },
    "thresholds": { "pinchClose": 0.50, "pinchOpen": 0.70 }
  }
}
```

`gestureDebug` exists so thresholds can be tuned by reading the numbers instead of guessing. The
engine sends its own thresholds alongside the measurements, so a client can draw them without
hardcoding values that would then drift from the engine's.

All distances are **multiples of hand scale**, never pixels or raw normalized units. See
`backend/airhand/gestures/features.py` for why: it is what makes one threshold work across
different cameras, lenses and hand-to-lens distances.

### Gesture semantics

`gesture` is a **state**, but clicks are **events**. A click is therefore latched for ~200 ms so a
client sampling at 10 Hz cannot miss it. While a pinch is held but not yet resolved into a click or
a drag, `gesture` reports `none` — reporting either would be a guess — and `gestureDebug.state`
shows `pinch_index_pending`. Consumers that need exact click timing should read engine-side state
transitions, not this sampled field.

### `error`
```jsonc
{ "type": "error",
  "code": "unauthorized" | "protocol_mismatch" | "camera_unavailable" | "internal"
        | "invalid_settings",
  "message": "..." }
```

`invalid_settings` (1.6.0) is the caller's problem, not a connection failure: the engine is healthy
and the previous settings are still in force. A client must not route it into its connection-error
state, or a slider dragged too far would read as "engine unavailable".

### Binary frames — camera preview

**In protocol 1.x, a binary server→client frame is a JPEG preview frame and nothing else.** No
envelope, no length prefix: the payload is the image. A second binary payload type would need a
major version bump, which is what the version field is for.

Added in 1.4.0, and sent only to clients that asked with `enable_preview`.

| Property | Value | Why |
|---|---|---|
| Width | `previewMaxWidth` in `protocol.json` (320) | The client blurs these into a background; more resolution is invisible and costs encode time and bytes linearly. |
| Height | Derived from the camera's aspect | A drifting aspect would put a stretched image under a correctly-proportioned landmark overlay. |
| Rate | `previewFps` (15) — a ceiling, not a guarantee | Whether a camera frame is due depends where its timestamp falls; measured ~11/s off a 30 fps camera. Undershooting is harmless, overshooting would eat the cursor's budget. |
| Size | ~6 KB measured | ~60 KB/s on loopback. |

The engine encodes on the same thread that drives the cursor, so it encodes **only while a client
is subscribed**, and stops the moment the last one disconnects. Measured cost when enabled: no
change to FPS or latency (30.4 fps / 11.5 ms both ways).

Preview frames are *not* meant to be drawn under the landmark overlay pixel-for-pixel. They are a
separate stream with its own timing, so any skew would read as inaccurate tracking. Blurred
background only.

## Client → server

### `auth`
```jsonc
{ "type": "auth", "token": "..." }
```

### `command`
```jsonc
{ "type": "command",
  "action": "start" | "stop" | "pause"
          | "enable_cursor" | "disable_cursor"
          | "enable_preview" | "disable_preview" }
```

Commands are argument-free verbs. Anything carrying data gets its own message type — see
`set_settings`.

`enable_preview` / `disable_preview` were added in 1.4.0 and control the binary preview stream.
Subscription is per connection and is dropped automatically on disconnect. Neither command
answers with a status message — preview is not part of engine state, it is a stream the caller
either receives or does not.

`enable_cursor` / `disable_cursor` were added in 1.3.0 and control OS actuation.

**Actuation is off on every launch and is never restored automatically.** It has to be requested
explicitly, each session — this deliberately overrides the "auto-start after calibration" rule
that governs *tracking*. That rule was written when nothing touched the OS; applied to actuation
it would hijack the pointer before the user could react.

An `enable_cursor` that cannot be honoured answers with an `error` carrying the reason, and
`status.cursorEnabled` stays `false`.

### `set_settings`
Added in 1.6.0. A **partial** patch — send only what changes.

```jsonc
{ "type": "set_settings", "gesture": { "pinchClose": 0.30 } }
{ "type": "set_settings", "reset": true }          // back to the engine's built-in defaults
```

A separate message type rather than a `command` payload: commands are argument-free verbs.

**All or nothing.** The engine validates the whole patch, and a single bad value refuses the lot
with `invalid_settings` while the previous settings stay in force. Applying the valid half would
leave the engine in a state nobody asked for, with no way for the client to learn which half
landed.

**Values are refused, never clamped.** These numbers steer OS input, and a `coverage` of 0.01
produces a pointer too twitchy to click the window in which you would undo it — so the lower bounds
are narrow rather than merely positive, and the kill-switch is the backstop. `bounds` in the
`settings` message is the authority; it comes from the engine's own definitions.

Accepted changes are persisted to `%LOCALAPPDATA%\AirHand\profile.json` and broadcast to every
client. **The profile belongs to the engine, not the UI** — `python -m airhand.main` has to keep
working standalone, so a calibration set from the desktop app must be in force on a launch the app
was never part of. It is a separate file from `runtime.json` because the handshake is deleted on
shutdown and the profile has to survive one.

A write that fails does not undo the change: it is already live, and the reason travels to the UI
in `profile.reason` instead.

### `calibrate`
Added in 1.7.0. Drives the Calibration wizard.

```jsonc
{ "type": "calibrate", "action": "start", "step": "pinch" }
{ "type": "calibrate", "action": "cancel" }
{ "type": "calibrate", "action": "complete" }
```

| Step | Duration | What the user does | What it suggests |
|---|---|---|---|
| `neutral` | 3 s | holds the hand where it is comfortable | `cursor.centerX` / `centerY` |
| `reach` | 6 s | reaches to their comfortable extremes | `cursor.coverage` |
| `pinch` | 8 s | three deliberate pinches | `gesture.pinchClose` / `pinchOpen` |

**`start` is refused unless tracking is running** — a measurement with no frames behind it would
show a countdown and then produce nothing.

**`start` disables cursor actuation.** The user is about to sweep a hand across the frame on
purpose; doing that while it drives the pointer would throw the cursor off the window they are
calibrating from. It is not re-enabled afterwards — actuation never auto-starts.

**`complete` sets `profile.calibrated`** and persists. It changes no setting: applying a
measurement is a `set_settings` like any other, and this only records that the wizard was finished.

### `ping`
```jsonc
{ "type": "ping", "ts": 1754647200.123 }
```

## Versioning

Semver on `version` in `protocol.json`:

- **patch** — docs/comments only, no wire change
- **minor** — additive optional fields; older clients keep working
- **major** — breaking; the client must refuse to connect

The client compares major versions and refuses on mismatch.

### Changelog

| Version | Change |
|---|---|
| 1.7.0 | Added the calibration channel: the `calibration` message, the `calibrate` command, `cursor.centerX` / `centerY`, `settings.activeArea` and `profile.calibrated`. Additive — a 1.6.x client ignores the new message and never sends a `calibrate`. |
| 1.6.0 | Added the settings channel: the `settings` message, the `set_settings` command, the `invalid_settings` error code and the `settings` capability. Additive — a 1.5.x client ignores the message and never sends a patch. |
| 1.5.0 | Added `frameWidth` / `frameHeight` to `status`, so a client can shape the landmark overlay to the camera instead of stretching it to fit. Additive — a 1.4.x client ignores them. |
| 1.4.0 | Added the camera preview stream: binary JPEG frames, `enable_preview` / `disable_preview`, the `preview` capability, and `previewMaxWidth` / `previewFps` in `protocol.json`. Additive — a 1.3.x client never asks and never receives one. |
| 1.3.0 | Added cursor actuation: `enable_cursor` / `disable_cursor` commands and the `cursor*` / `killswitchHotkey` fields on `status`. Additive — a 1.2.x client simply never enables it. |
| 1.2.0 | Added `gestureDebug` to `telemetry`. Additive and optional — omitted when no hand is present, so a 1.1.x client is unaffected. |
| 1.1.0 | Added `captureMs` and `inferenceMs` to `telemetry`. Additive and optional — a 1.0.0 client ignores them and keeps working. |
| 1.0.0 | Initial contract. |
