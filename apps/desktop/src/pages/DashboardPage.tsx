import { Camera, Gauge, Hand, Timer, TriangleAlert, Video } from 'lucide-react'

import { CameraPreview } from '@/components/CameraPreview'
import { StatusTile, type Tone } from '@/components/StatusTile'
import { useNow } from '@/hooks/useNow'
import { gestureLabel } from '@/lib/protocol'
import { useConnectionStore } from '@/stores/connectionStore'
import { pipelineIssue, useTrackingStore } from '@/stores/trackingStore'

/** Targets from projectbrief.md. The Dashboard must make it obvious when they are not being met. */
const FPS_TARGET_MIN = 30
const LATENCY_TARGET_MS = 50

/**
 * The hand, large, and the numbers that qualify it.
 *
 * Tracking and cursor controls live in the app shell rather than here, so they are reachable from
 * every screen. What is left is the one thing this page is for: seeing what the engine sees.
 */

/** Keyed so the lookup is total, including the case where the engine did not classify the hand. */
const HANDEDNESS_LABEL: Record<'left' | 'right' | 'unknown', string> = {
  left: 'Left',
  right: 'Right',
  unknown: 'Unknown',
}

export function DashboardPage() {
  const now = useNow()

  const phase = useConnectionStore((state) => state.phase)
  const connectionError = useConnectionStore((state) => state.error)
  const connected = phase === 'connected'

  // Narrow selectors: an FPS tick must not re-render anything that does not display FPS.
  const camera = useTrackingStore((state) => state.camera)
  const cameraName = useTrackingStore((state) => state.cameraName)
  const tracking = useTrackingStore((state) => state.tracking)
  const fps = useTrackingStore((state) => state.fps)
  const latencyMs = useTrackingStore((state) => state.latencyMs)
  const captureMs = useTrackingStore((state) => state.captureMs)
  const inferenceMs = useTrackingStore((state) => state.inferenceMs)
  const handDetected = useTrackingStore((state) => state.handDetected)
  const handedness = useTrackingStore((state) => state.handedness)
  const gesture = useTrackingStore((state) => state.gesture)
  const lastSampleAt = useTrackingStore((state) => state.lastSampleAt)

  const issue = pipelineIssue(connected, { camera, tracking, handDetected, lastSampleAt }, now)
  const streaming = connected && tracking === 'running' && issue !== 'stream-stalled'

  // If waiting on frames dominates the frame budget, the camera is the limit — not the model.
  // Saying so beats making someone infer it from two numbers that look unrelated.
  const cameraBound = captureMs > latencyMs

  const fpsTone: Tone = !streaming ? 'idle' : fps >= FPS_TARGET_MIN ? 'live' : 'degraded'
  const latencyTone: Tone = !streaming ? 'idle' : latencyMs <= LATENCY_TARGET_MS ? 'live' : 'degraded'
  const cameraTone: Tone =
    camera === 'error' ? 'down' : camera === 'on' ? 'live' : camera === 'starting' ? 'degraded' : 'idle'
  const trackingTone: Tone =
    !connected ? 'down' : tracking === 'running' ? 'live' : tracking === 'paused' ? 'degraded' : 'idle'
  const gestureActive = streaming && handDetected && gesture !== 'none'

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      {connectionError ? (
        <div
          className="flex gap-3 rounded-xl border border-status-down/30 bg-status-down/10 p-4 backdrop-blur-xl"
          role="alert"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-status-down" aria-hidden />
          <div className="min-w-0">
            <p className="text-sm font-medium text-status-down">
              Engine unavailable ({connectionError.code})
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {connectionError.message}
            </p>
          </div>
        </div>
      ) : null}

      {/* min-h-0 all the way down, or the flex child refuses to shrink and the strip is pushed off. */}
      <section className="min-h-0 flex-1">
        <CameraPreview issue={issue} />
      </section>

      <section className="grid shrink-0 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatusTile
          label="FPS"
          value={streaming ? fps.toFixed(1) : '—'}
          hint={
            streaming && cameraBound
              ? `camera-bound · ${captureMs.toFixed(0)} ms waiting`
              : `target ${FPS_TARGET_MIN}–60`
          }
          tone={fpsTone}
          icon={Gauge}
          numeric
          dense
        />
        <StatusTile
          label="Latency"
          value={streaming ? `${latencyMs.toFixed(1)} ms` : '—'}
          hint={
            streaming && inferenceMs > 0
              ? `${inferenceMs.toFixed(1)} ms inference`
              : `target < ${LATENCY_TARGET_MS} ms`
          }
          tone={latencyTone}
          icon={Timer}
          numeric
          dense
        />
        <StatusTile
          label="Camera"
          value={CAMERA_LABEL[camera]}
          hint={cameraName}
          tone={cameraTone}
          icon={Camera}
          dense
        />
        <StatusTile
          label="Tracking"
          value={connected ? TRACKING_LABEL[tracking] : 'Disconnected'}
          hint={connected ? null : 'Engine not reachable'}
          tone={trackingTone}
          icon={Video}
          dense
        />
        <StatusTile
          label="Hand"
          // Three states, not two. The engine reports `null` when it has landmarks but no
          // classification, and the old ternary turned that into a confident "Right" — inventing
          // a fact rather than admitting it does not have one. Same rule that makes `activeArea`
          // null until the screen and the camera are both known.
          value={handDetected ? HANDEDNESS_LABEL[handedness ?? 'unknown'] : 'None'}
          hint={handDetected ? '21 landmarks' : 'No hand in view'}
          tone={handDetected ? 'live' : 'idle'}
          icon={Hand}
          dense
        />
        <StatusTile
          label="Gesture"
          value={streaming && handDetected ? gestureLabel(gesture) : '—'}
          hint={gestureActive ? 'recognised' : 'idle'}
          tone={gestureActive ? 'live' : 'idle'}
          icon={Hand}
          dense
        />
      </section>
    </div>
  )
}

const CAMERA_LABEL = {
  off: 'Off',
  starting: 'Starting',
  on: 'On',
  error: 'Error',
} as const

const TRACKING_LABEL = {
  idle: 'Stopped',
  running: 'Running',
  paused: 'Paused',
} as const
