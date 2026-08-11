import { create } from 'zustand'

import type { CameraState, Gesture, GestureDebug, TrackingState } from '@/lib/protocol'

/**
 * Pipeline state for React to render.
 *
 * Two different cadences feed this store:
 *  - `applyStatus` — pushed by the engine on change only (low frequency).
 *  - `applyReadout` — sampled from the telemetry buffer by the pump at READOUT_HZ, NOT once per
 *    message. Numbers a human reads have no business updating 60 times a second.
 */

export const READOUT_HZ = 10

interface TrackingSnapshot {
  camera: CameraState
  tracking: TrackingState
  cameraName: string | null
  /**
   * Which device the current status describes, null before one is open.
   *
   * **Not the same as `camerasStore.selected`**, which is what the engine opens *next*: a device
   * chosen while the pipeline is stopped has not been opened yet, and after one fails to open the
   * two disagree permanently until something succeeds. The picker renders both.
   */
  cameraIndex: number | null
  cpuPercent: number
  statusMessage: string | null
  /**
   * Shape of the frame the landmarks are normalized against. Null until the camera is open.
   *
   * The overlay must be shaped from this rather than stretched to fill its container — `x` is
   * normalized by width and `y` by height, so any other aspect distorts the hand along one axis.
   */
  frameWidth: number | null
  frameHeight: number | null

  /** Cursor actuation — all from `status`, all authoritative. */
  cursorAvailable: boolean
  cursorEnabled: boolean
  cursorReason: string | null
  cursorDryRun: boolean
  killswitchHotkey: string | null

  fps: number
  latencyMs: number
  /** Time the engine spent blocked on the camera. Explains throughput, not latency. */
  captureMs: number
  /** The model's share of latency. */
  inferenceMs: number
  handDetected: boolean
  handedness: 'left' | 'right' | null
  gesture: Gesture
  /** Wall-clock ms of the last sample the pump observed; null when the stream is silent. */
  lastSampleAt: number | null
  /** Raw feature values for threshold tuning. Null when no hand is present. */
  gestureDebug: GestureDebug | null
}

interface TrackingState_ extends TrackingSnapshot {
  applyStatus: (
    status: Pick<
      TrackingSnapshot,
      | 'camera'
      | 'tracking'
      | 'cameraName'
      | 'cameraIndex'
      | 'cpuPercent'
      | 'frameWidth'
      | 'frameHeight'
      | 'cursorAvailable'
      | 'cursorEnabled'
      | 'cursorReason'
      | 'cursorDryRun'
      | 'killswitchHotkey'
    > & {
      statusMessage: string | null
    },
  ) => void
  applyReadout: (
    readout: Pick<
      TrackingSnapshot,
      | 'fps'
      | 'latencyMs'
      | 'captureMs'
      | 'inferenceMs'
      | 'handDetected'
      | 'handedness'
      | 'gesture'
      | 'lastSampleAt'
    >,
  ) => void
  /**
   * Kept out of `applyReadout` on purpose: its float fields change every frame, so folding it
   * into that call would defeat the "skip identical readouts" check and make the tuning values
   * freeze whenever FPS happened to be steady.
   */
  applyGestureDebug: (debug: GestureDebug | null) => void
  clearStream: () => void
  reset: () => void
}

const INITIAL: TrackingSnapshot = {
  camera: 'off',
  tracking: 'idle',
  cameraName: null,
  cameraIndex: null,
  cpuPercent: 0,
  statusMessage: null,
  frameWidth: null,
  frameHeight: null,

  cursorAvailable: false,
  cursorEnabled: false,
  cursorReason: null,
  cursorDryRun: false,
  killswitchHotkey: null,

  fps: 0,
  latencyMs: 0,
  captureMs: 0,
  inferenceMs: 0,
  handDetected: false,
  handedness: null,
  gesture: 'none',
  lastSampleAt: null,
  gestureDebug: null,
}

export const useTrackingStore = create<TrackingState_>((set) => ({
  ...INITIAL,

  applyStatus: (status) => set(status),

  applyReadout: (readout) =>
    set((state) => {
      // Skip the update when nothing a user could see has changed. At 10 Hz against a static
      // scene this keeps the tree entirely still.
      if (
        state.fps === readout.fps &&
        state.latencyMs === readout.latencyMs &&
        state.handDetected === readout.handDetected &&
        state.handedness === readout.handedness &&
        state.gesture === readout.gesture
      ) {
        return state
      }
      return readout
    }),

  applyGestureDebug: (gestureDebug) =>
    set((state) => (state.gestureDebug === gestureDebug ? state : { gestureDebug })),

  clearStream: () =>
    set({
      fps: 0,
      latencyMs: 0,
      captureMs: 0,
      inferenceMs: 0,
      handDetected: false,
      handedness: null,
      gesture: 'none',
      lastSampleAt: null,
      gestureDebug: null,
    }),

  reset: () => set({ ...INITIAL }),
}))

/**
 * Why the pipeline is not producing usable tracking, or null when it is.
 *
 * The three failure modes stay distinct on purpose: "backend down", "camera off" and
 * "no hand detected" are different problems and the user needs to know which one they have.
 */
export type PipelineIssue =
  | 'disconnected'
  | 'camera-error'
  | 'camera-off'
  | 'not-tracking'
  | 'no-hand'
  | 'stream-stalled'

export function pipelineIssue(
  connected: boolean,
  snapshot: Pick<TrackingSnapshot, 'camera' | 'tracking' | 'handDetected' | 'lastSampleAt'>,
  now: number,
): PipelineIssue | null {
  if (!connected) return 'disconnected'
  if (snapshot.camera === 'error') return 'camera-error'
  if (snapshot.camera === 'off' || snapshot.camera === 'starting') return 'camera-off'
  if (snapshot.tracking !== 'running') return 'not-tracking'
  if (snapshot.lastSampleAt === null || now - snapshot.lastSampleAt > 1000) return 'stream-stalled'
  if (!snapshot.handDetected) return 'no-hand'
  return null
}
