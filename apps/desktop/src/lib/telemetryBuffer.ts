/**
 * Latest-sample buffer for the telemetry stream.
 *
 * Telemetry arrives at ~60 Hz. Routing that through React state would re-render the tree 60 times
 * a second and starve the CV pipeline of CPU — systemPatterns.md forbids it. Instead every
 * message overwrites a single mutable slot here, and consumers sample it on their own schedule:
 * the canvas overlay on requestAnimationFrame, the numeric readouts at a much slower cadence.
 *
 * Deliberately not a store: there is no subscription and no change notification. Reading is a
 * plain property access, so a message that nobody samples costs nothing beyond the assignment.
 */

import type { TelemetryMessage } from './protocol'

let latest: TelemetryMessage | null = null
let receivedCount = 0

export function pushTelemetry(sample: TelemetryMessage): void {
  latest = sample
  receivedCount += 1
}

export function readTelemetry(): TelemetryMessage | null {
  return latest
}

/** Total messages received since load — used to detect a stalled stream. */
export function telemetryCount(): number {
  return receivedCount
}

export function clearTelemetry(): void {
  latest = null
}
