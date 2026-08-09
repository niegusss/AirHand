/**
 * The single requestAnimationFrame loop that drives everything telemetry-shaped.
 *
 * One loop, not one per component: each frame it samples the latest-sample buffer once, hands
 * that sample to frame subscribers (the canvas overlay), and — at the much slower READOUT_HZ —
 * pushes numeric readouts into the Zustand store for React to render.
 *
 * This is where "throttle at the boundary" from systemPatterns.md actually happens.
 */

import { readTelemetry } from './telemetryBuffer'
import type { TelemetryMessage } from './protocol'
import { READOUT_HZ, useTrackingStore } from '@/stores/trackingStore'

export type FrameSubscriber = (sample: TelemetryMessage | null) => void

const subscribers = new Set<FrameSubscriber>()
const READOUT_INTERVAL_MS = 1000 / READOUT_HZ

let rafHandle: number | null = null
let lastReadoutAt = 0
/**
 * Timestamp of the last *distinct* sample observed.
 *
 * The buffer keeps returning the most recent message forever, so freshness has to be judged by
 * the sample's own `ts`. Using wall-clock time here instead would mean a stalled stream looked
 * perfectly healthy — the readout would keep re-stamping the same stale frame as "just arrived".
 */
let lastSeenTs: number | null = null
let lastSeenAt: number | null = null

function tick(now: number): void {
  const sample = readTelemetry()

  for (const subscriber of subscribers) {
    subscriber(sample)
  }

  if (sample !== null && sample.ts !== lastSeenTs) {
    lastSeenTs = sample.ts
    lastSeenAt = Date.now()
  }

  if (now - lastReadoutAt >= READOUT_INTERVAL_MS) {
    lastReadoutAt = now
    const store = useTrackingStore.getState()
    store.applyGestureDebug(sample?.gestureDebug ?? null)

    if (sample === null) {
      store.applyReadout({
        fps: 0,
        latencyMs: 0,
        captureMs: 0,
        inferenceMs: 0,
        handDetected: false,
        handedness: null,
        gesture: 'none',
        lastSampleAt: null,
      })
    } else {
      store.applyReadout({
        fps: sample.fps,
        latencyMs: sample.latencyMs,
        // Optional since protocol 1.1.0 — a 1.0.x engine simply reports nothing here.
        captureMs: sample.captureMs ?? 0,
        inferenceMs: sample.inferenceMs ?? 0,
        handDetected: sample.handDetected,
        handedness: sample.handedness,
        gesture: sample.gesture,
        lastSampleAt: lastSeenAt,
      })
    }
  }

  rafHandle = requestAnimationFrame(tick)
}

export function startPump(): void {
  if (rafHandle !== null) return
  lastReadoutAt = 0
  lastSeenTs = null
  lastSeenAt = null
  rafHandle = requestAnimationFrame(tick)
}

export function stopPump(): void {
  if (rafHandle === null) return
  cancelAnimationFrame(rafHandle)
  rafHandle = null
}

export function subscribeToFrames(subscriber: FrameSubscriber): () => void {
  subscribers.add(subscriber)
  return () => {
    subscribers.delete(subscriber)
  }
}
