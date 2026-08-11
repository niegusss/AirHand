/**
 * Wire types for the engine <-> UI protocol.
 *
 * The version and landmark count are injected at build time from shared/protocol/protocol.json —
 * the same file the Python engine reads. See shared/protocol/README.md for the full contract.
 */

export const PROTOCOL_VERSION = __PROTOCOL_VERSION__
export const LANDMARK_COUNT = __LANDMARK_COUNT__

export type CameraState = 'off' | 'starting' | 'on' | 'error'
export type TrackingState = 'idle' | 'running' | 'paused'
export type Gesture = 'none' | 'move' | 'left_click' | 'right_click' | 'drag' | 'scroll'
export type ErrorCode =
  | 'unauthorized'
  | 'protocol_mismatch'
  | 'camera_unavailable'
  | 'internal'
  /** A settings patch the engine will not accept. Added in protocol 1.6.0; the previous values
   * stay in force, so there is nothing to roll back. */
  | 'invalid_settings'

/** A landmark is [x, y, z]; x and y are normalized 0..1. */
export type Landmark = [number, number, number]

export interface HelloMessage {
  type: 'hello'
  protocolVersion: string
  engineVersion: string
  capabilities: string[]
}

export interface StatusMessage {
  type: 'status'
  camera: CameraState
  tracking: TrackingState
  cameraName: string | null
  /**
   * Which device this status describes. Added in protocol 1.10.0, null for a source with no notion
   * of a device index.
   *
   * `cameraName` embeds the index in a display string, and matching a device against the
   * discovered list by parsing that string would be exactly the second copy this protocol keeps
   * removing.
   */
  cameraIndex?: number | null
  cpuPercent: number
  message: string | null
  /**
   * Shape of the frame the landmarks are normalized against. Added in protocol 1.5.0, null until
   * the camera is open.
   *
   * MediaPipe divides `x` by width and `y` by height, so an overlay drawn into a container of a
   * different aspect stretches the hand along one axis. Never assume 4:3 — there is no reference
   * webcam, which is why the engine reports this instead of the client guessing.
   */
  frameWidth?: number | null
  frameHeight?: number | null
  /** Can this machine actuate the cursor at all? Added in protocol 1.3.0. */
  cursorAvailable?: boolean
  /**
   * Whether the engine is actuating **right now**.
   *
   * Authoritative, not an echo of what the UI requested: the engine disables actuation on its own
   * via the kill-switch, a pause, or the last client disconnecting. Rendering the local request
   * instead would show a control that lies about the state of the machine.
   */
  cursorEnabled?: boolean
  /** Why actuation is unavailable. */
  cursorReason?: string | null
  /** Actions are logged instead of performed. */
  cursorDryRun?: boolean
  /** Global hotkey that disables actuation, in pynput syntax. */
  killswitchHotkey?: string | null
}

export interface TelemetryMessage {
  type: 'telemetry'
  ts: number
  fps: number
  /**
   * Frame in hand → landmarks ready. Excludes sensor exposure, driver buffering, transport and
   * render — a floor for end-to-end latency, not the number itself.
   */
  latencyMs: number
  /**
   * Time the engine blocked waiting for a frame. Explains THROUGHPUT, not latency: the frame is
   * fresh on arrival, so this is idle time. Low FPS with low `latencyMs` means it went here.
   * Added in protocol 1.1.0 — optional, so a 1.0.x engine still works.
   */
  captureMs?: number
  /** The model's share of `latencyMs`. Added in protocol 1.1.0. */
  inferenceMs?: number
  handDetected: boolean
  handedness: 'left' | 'right' | null
  gesture: Gesture
  landmarks: Landmark[] | null
  cursor: { x: number; y: number } | null
  /** Added in protocol 1.2.0; absent when no hand is detected. */
  gestureDebug?: GestureDebug
}

export type FingerName = 'thumb' | 'index' | 'middle' | 'ring' | 'pinky'

/**
 * Raw feature values behind the classification. Added in protocol 1.2.0, omitted when no hand is
 * present.
 *
 * All distances are multiples of `handScale`, never pixels — that is what makes one threshold work
 * across different cameras, lenses and hand-to-lens distances. The engine sends its own thresholds
 * alongside so a client can draw them without keeping a second copy that would drift.
 */
export interface GestureDebug {
  /** Internal FSM state — richer than `gesture`; includes undecided states like `pinch_index_pending`. */
  state: string
  /** Reference length (wrist → middle MCP) in isotropic units. */
  handScale: number
  pinchIndex: number
  pinchMiddle: number
  extended: Record<FingerName, boolean>
  /** Curl angle at each finger's middle joint, degrees. 180 is straight. */
  angles: Record<FingerName, number>
  thresholds: { pinchClose: number; pinchOpen: number }
}

/**
 * Everything the Calibration screen can change. Added in protocol 1.6.0.
 *
 * Sections mirror the engine's own grouping: gesture thresholds, pointer smoothing, cursor
 * mapping. Wire names are camelCase; the engine's attributes are snake_case and the translation
 * happens there, once.
 */
export interface EngineSettings {
  gesture: {
    pinchClose: number
    pinchOpen: number
    holdToDragSeconds: number
    clickLatchSeconds: number
    extendedAngleDegrees: number
    scrollStep: number
    /**
     * How long detection may drop out before pinch state is discarded.
     *
     * A click is defined by the release, so the engine has to remember the pinch was closed — and
     * a pinch is exactly when the thumb disappears behind the index finger. Zero restores the old
     * behaviour, where a single lost frame swallowed the click.
     */
    dropoutGraceSeconds: number
  }
  pointer: {
    minCutoff: number
    beta: number
    dCutoff: number
    holdOnPinch: boolean
    dropoutGraceSeconds: number
  }
  cursor: {
    coverage: number
    /**
     * Where the active area sits in the frame. Added in protocol 1.7.0.
     *
     * The default is dead centre, which is what everyone got before Calibration could measure a
     * resting position — a user sitting to one side of the camera would otherwise have to reach
     * across it to touch the edge of the screen.
     */
    centerX: number
    centerY: number
  }
}

/**
 * The sub-rectangle of the camera frame that maps onto the whole screen, in normalized frame
 * coordinates. Added in protocol 1.7.0; null until both the screen and the camera are known.
 *
 * Sent by the engine rather than derived here. Computing it needs the screen aspect and the frame
 * aspect and gets the aspect handling wrong in ways that stay invisible until an ultrawide meets a
 * 4:3 webcam — and a copy of that arithmetic in TypeScript would be computer-vision logic in the
 * frontend, which this project does not allow.
 */
export interface ActiveArea {
  left: number
  top: number
  width: number
  height: number
}

/** `[low, high]` per numeric knob; null for booleans, which have no range. */
export type SettingsBounds = {
  [Section in keyof EngineSettings]: {
    [Knob in keyof EngineSettings[Section]]: [number, number] | null
  }
}

/** Where the calibration profile lives and whether it is in force. Null when the engine was
 * started with `--no-profile`, i.e. nothing is being persisted. */
export interface ProfileInfo {
  path: string
  loaded: boolean
  /**
   * A profile exists but was refused — most often because it was calibrated against a different
   * MediaPipe model. The UI should offer a re-calibration rather than showing defaults as though
   * nothing had ever been saved.
   */
  stale: boolean
  reason: string | null
  /**
   * Whether the calibration wizard has been completed. Added in protocol 1.7.0.
   *
   * **Not the same thing as `loaded`.** Every settings change writes the profile, so a file exists
   * the moment anyone nudges a slider — which would satisfy a "calibration is mandatory on first
   * run" check without anyone having calibrated. Only this field answers that question.
   */
  calibrated: boolean
  /**
   * When the profile was last written, ISO 8601 with a UTC offset. Added in protocol 1.9.0.
   *
   * Null for a profile that was never saved, or one written before the engine recorded this. Kept
   * in the file rather than read from its mtime, which copying or syncing resets.
   */
  savedAt: string | null
}

/**
 * One capture device. Added in protocol 1.10.0.
 *
 * Identified by index and backend rather than by a friendly name: OpenCV cannot supply one on
 * Windows without a DirectShow enumeration dependency, and this app ships offline with no optional
 * extras. `fps` and `fourcc` are deliberately not on the wire — discovery opens each device without
 * requesting a format, so they would describe the driver's idle default rather than anything the
 * pipeline would get.
 */
export interface CameraDevice {
  index: number
  name: string
  width: number
  height: number
}

/**
 * The device list and the current selection. Added in protocol 1.10.0.
 *
 * Broadcast like `settings` and `calibration`, because a scan restarts the pipeline and every
 * window has to learn about it.
 */
export interface CamerasMessage {
  type: 'cameras'
  /** A probe is in flight. The pipeline is down for its duration. */
  scanning: boolean
  /**
   * Empty until someone scans, which is not an error. Probing opens and releases each device in
   * turn, so it is never done on connect — reconnects happen on their own and would otherwise
   * blink the camera for reasons the user never triggered.
   */
  devices: CameraDevice[]
  /**
   * What the engine will open **next**, which is not always what it has open now — a device chosen
   * while the pipeline is stopped takes effect on the next start. `status.cameraIndex` answers the
   * other question.
   *
   * Null when the user has never chosen one. The engine's default is also index 0, so null and 0
   * have to stay distinguishable.
   */
  selected: number | null
  /** Why a scan or a selection could not be honoured, meant to be shown. */
  reason: string | null
}

export type CalibrationStep = 'neutral' | 'reach' | 'pinch'
export type CalibrationState = 'sampling' | 'done' | 'failed'

/**
 * A measurement in progress, or its verdict. Added in protocol 1.7.0.
 *
 * Broadcast to every client, like `settings` and for the same reason: two windows must not
 * disagree about where the wizard is.
 */
export interface CalibrationMessage {
  type: 'calibration'
  step: CalibrationStep
  state: CalibrationState
  samples: number
  secondsRemaining: number
  /** How long this step runs in total, so a progress bar needs no copy of the step durations. */
  secondsTotal: number
  /**
   * Which instruction to show right now, for a step that asks for more than one thing.
   * Added in protocol 1.8.0; null for steps with a single pose, and null once a step has finished.
   *
   * The pinch step has two: deliberate pinches, then a closed hand. The second bounds the
   * threshold from above and cannot be inferred from the first — a fist and a pinch overlap in
   * the measurement, which is precisely the problem it exists to solve.
   */
  phase: string | null
  /** Raw numbers behind the verdict. Present even on failure, so the user can see what was seen. */
  measurement: Record<string, number | null> | null
  /**
   * A `set_settings` patch, verbatim — accepting a measurement is the ordinary settings path with
   * the ordinary validation. Null whenever the engine will not stand behind a number.
   */
  suggestion: SettingsPatch | null
  /** Why it failed, meant to be shown. */
  reason: string | null
}

/**
 * Current settings, plus the limits and defaults they must be edited within.
 *
 * Sent after `status` on connect and re-broadcast to **every** client on change — the engine is
 * authoritative here for the same reason it is for `cursorEnabled`. Render what arrived, never
 * what was requested.
 *
 * `bounds` and `defaults` travel on the wire so no client keeps its own copy of the ranges. A
 * second copy is one that eventually disagrees with the engine's, and the disagreement shows up as
 * a slider that lets you pick a value the engine then refuses.
 */
export interface SettingsMessage extends EngineSettings {
  type: 'settings'
  bounds: SettingsBounds
  defaults: EngineSettings
  profile: ProfileInfo | null
  /** Added in protocol 1.7.0. Null until the engine knows both the screen and the camera. */
  activeArea: ActiveArea | null
}

/** A partial patch — only what changes. `reset` restores the engine's built-in defaults. */
export interface SettingsPatch {
  gesture?: Partial<EngineSettings['gesture']>
  pointer?: Partial<EngineSettings['pointer']>
  cursor?: Partial<EngineSettings['cursor']>
  reset?: boolean
}

export interface ErrorMessage {
  type: 'error'
  code: ErrorCode
  message: string
}

export interface PongMessage {
  type: 'pong'
  ts: number
}

export type ServerMessage =
  | HelloMessage
  | StatusMessage
  | SettingsMessage
  | CalibrationMessage
  | CamerasMessage
  | TelemetryMessage
  | ErrorMessage
  | PongMessage

export type CommandAction =
  | 'start'
  | 'stop'
  | 'pause'
  | 'enable_cursor'
  | 'disable_cursor'
  /**
   * Camera preview stream, added in protocol 1.4.0.
   *
   * Opt-in because the engine encodes frames on the same thread that drives the cursor. A client
   * that never asks costs the pipeline nothing.
   */
  | 'enable_preview'
  | 'disable_preview'
  /**
   * Scan for capture devices, added in protocol 1.10.0.
   *
   * The one command that takes the pipeline down and puts it back: an open device cannot be
   * enumerated on Windows, so a probe that ran mid-capture would omit the camera in use.
   */
  | 'discover_cameras'

export type ClientMessage =
  | { type: 'auth'; token: string }
  | { type: 'command'; action: CommandAction }
  /**
   * Propose a settings change. Added in protocol 1.6.0.
   *
   * A separate message type rather than a `command` payload: commands are argument-free verbs and
   * the engine's handler takes only an action string. Named asymmetrically to the server's
   * `settings` so the direction is visible at the call site.
   */
  | ({ type: 'set_settings' } & SettingsPatch)
  /**
   * Drive the calibration wizard. Added in protocol 1.7.0.
   *
   * `start` is refused unless tracking is running, and disables cursor actuation for the duration
   * — the user is about to sweep a hand across the frame deliberately. `complete` records that the
   * wizard was finished; it changes no setting, because applying a measurement is an ordinary
   * `set_settings`.
   */
  | { type: 'calibrate'; action: 'start'; step: CalibrationStep }
  | { type: 'calibrate'; action: 'cancel' | 'complete' }
  /**
   * Choose a capture device. Added in protocol 1.10.0.
   *
   * Its own message type rather than a `command` payload, for the same reason as `set_settings`:
   * it carries data, and commands are argument-free verbs.
   */
  | { type: 'select_camera'; index: number }
  | { type: 'ping'; ts: number }

/**
 * Is this a binary frame — i.e. a camera preview JPEG?
 *
 * In protocol 1.x, binary server→client means exactly one thing. A second binary payload type
 * would need a major version bump, which is what the version field is for.
 */
export function isPreviewFrame(raw: unknown): raw is Blob | ArrayBuffer {
  return raw instanceof Blob || raw instanceof ArrayBuffer
}

/**
 * Parse and shape-check an inbound text frame.
 *
 * Returns null for anything unrecognized. A malformed message must never reach the rest of the
 * app — on the engine side the equivalent guard stops one from driving an OS input event.
 */
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function parseServerMessage(raw: unknown): ServerMessage | null {
  if (typeof raw !== 'string') return null

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }

  if (typeof parsed !== 'object' || parsed === null) return null
  const message = parsed as Record<string, unknown>

  switch (message.type) {
    case 'hello':
      return typeof message.protocolVersion === 'string' ? (message as unknown as HelloMessage) : null
    case 'status':
      return typeof message.camera === 'string' && typeof message.tracking === 'string'
        ? (message as unknown as StatusMessage)
        : null
    case 'settings':
      // Checked on the sections, not on every knob: the engine sends all of them or none, and a
      // per-field check here would be the second copy of the schema this design avoids.
      return isObject(message.gesture) && isObject(message.pointer) && isObject(message.cursor)
        ? (message as unknown as SettingsMessage)
        : null
    case 'calibration':
      // `step` and `state` are what the UI branches on, so those are the two worth checking. The
      // measurement and the suggestion are allowed to be null and are rendered defensively.
      return typeof message.step === 'string' && typeof message.state === 'string'
        ? (message as unknown as CalibrationMessage)
        : null
    case 'cameras':
      // The list is what everything else on the screen is derived from, and an empty one is a
      // legitimate state — so its presence and type are the check, not its length.
      return Array.isArray(message.devices) ? (message as unknown as CamerasMessage) : null
    case 'telemetry':
      return typeof message.fps === 'number' && typeof message.gesture === 'string'
        ? (message as unknown as TelemetryMessage)
        : null
    case 'error':
      return typeof message.code === 'string' ? (message as unknown as ErrorMessage) : null
    case 'pong':
      return message as unknown as PongMessage
    default:
      return null
  }
}

/** Major versions must match exactly; the client refuses to interoperate across a major bump. */
export function isProtocolCompatible(serverVersion: string): boolean {
  const [serverMajor] = serverVersion.split('.')
  const [clientMajor] = PROTOCOL_VERSION.split('.')
  return serverMajor === clientMajor
}

const GESTURE_LABELS: Record<Gesture, string> = {
  none: 'None',
  move: 'Move',
  left_click: 'Left click',
  right_click: 'Right click',
  drag: 'Drag',
  scroll: 'Scroll',
}

export function gestureLabel(gesture: Gesture): string {
  return GESTURE_LABELS[gesture] ?? 'Unknown'
}
