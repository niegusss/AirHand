import { Activity, Cpu, Gauge, Hand, Timer } from 'lucide-react'

import { GestureIndicator } from '@/components/GestureIndicator'
import { PinchMeter } from '@/components/PinchMeter'
import { cn } from '@/lib/utils'
import { useNow } from '@/hooks/useNow'
import type { FingerName } from '@/lib/protocol'
import { useConnectionStore } from '@/stores/connectionStore'
import { pipelineIssue, useTrackingStore } from '@/stores/trackingStore'

const FINGERS: FingerName[] = ['thumb', 'index', 'middle', 'ring', 'pinky']

const FINGER_LABELS: Record<FingerName, string> = {
  thumb: 'Thumb',
  index: 'Index',
  middle: 'Middle',
  ring: 'Ring',
  pinky: 'Pinky',
}

/**
 * Tuning and performance view.
 *
 * Gesture thresholds cannot be tuned by guessing, so this screen shows the raw features the
 * classifier actually keys on. It exists because the current pass deliberately stops short of
 * driving the OS cursor: tune here first, actuate later.
 */
export function DiagnosticsPage() {
  const now = useNow()

  const phase = useConnectionStore((state) => state.phase)
  const serverProtocol = useConnectionStore((state) => state.serverProtocolVersion)
  const engineVersion = useConnectionStore((state) => state.engineVersion)
  const endpoint = useConnectionStore((state) => state.endpointUrl)
  const connected = phase === 'connected'

  const camera = useTrackingStore((state) => state.camera)
  const cameraName = useTrackingStore((state) => state.cameraName)
  const frameWidth = useTrackingStore((state) => state.frameWidth)
  const frameHeight = useTrackingStore((state) => state.frameHeight)
  const tracking = useTrackingStore((state) => state.tracking)
  const handDetected = useTrackingStore((state) => state.handDetected)
  const lastSampleAt = useTrackingStore((state) => state.lastSampleAt)
  const fps = useTrackingStore((state) => state.fps)
  const latencyMs = useTrackingStore((state) => state.latencyMs)
  const captureMs = useTrackingStore((state) => state.captureMs)
  const inferenceMs = useTrackingStore((state) => state.inferenceMs)
  const gesture = useTrackingStore((state) => state.gesture)
  const debug = useTrackingStore((state) => state.gestureDebug)
  const cursorAvailable = useTrackingStore((state) => state.cursorAvailable)
  const cursorEnabled = useTrackingStore((state) => state.cursorEnabled)
  const cursorReason = useTrackingStore((state) => state.cursorReason)
  const cursorDryRun = useTrackingStore((state) => state.cursorDryRun)
  const killswitchHotkey = useTrackingStore((state) => state.killswitchHotkey)

  const issue = pipelineIssue(connected, { camera, tracking, handDetected, lastSampleAt }, now)
  const streaming = connected && tracking === 'running' && issue !== 'stream-stalled'
  const cameraBound = captureMs > latencyMs

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-5">
      <div>
        <div className="flex items-center gap-2.5">
          <Activity className="size-5 text-muted-foreground" aria-hidden />
          <h1 className="text-xl font-semibold tracking-tight">Diagnostics</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Performance against target, and the raw features behind gesture classification.
        </p>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          icon={Gauge}
          label="FPS"
          value={streaming ? fps.toFixed(1) : '—'}
          note={cameraBound && streaming ? 'camera-bound' : 'target 30–60'}
        />
        <Metric
          icon={Timer}
          label="Latency"
          value={streaming ? `${latencyMs.toFixed(1)} ms` : '—'}
          note="frame → landmarks"
        />
        <Metric
          icon={Cpu}
          label="Inference"
          value={streaming ? `${inferenceMs.toFixed(1)} ms` : '—'}
          note="model only"
        />
        <Metric
          icon={Timer}
          label="Capture wait"
          value={streaming ? `${captureMs.toFixed(1)} ms` : '—'}
          note="throughput, not latency"
        />
      </section>

      <p className="surface p-3 text-xs leading-relaxed text-muted-foreground">
        <strong className="font-medium text-foreground">Latency is a floor.</strong> It covers
        frame-in-hand → landmarks-ready only, excluding sensor exposure, driver buffering, transport
        and render. <strong className="font-medium text-foreground">Capture wait</strong> is idle
        time spent waiting for the next frame — it explains throughput, not staleness.
      </p>

      {/* The full vocabulary, next to the numbers that decide it — this is the tuning screen. */}
      <GestureIndicator gesture={gesture} active={streaming && handDetected} />

      <section className="surface p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Gesture features
          </h2>
          {debug ? (
            <code className="rounded-md bg-secondary px-2 py-0.5 text-xs text-foreground">
              {debug.state}
            </code>
          ) : null}
        </div>

        {debug ? (
          <div className="mt-4 flex flex-col gap-5">
            <div className="grid gap-5 sm:grid-cols-2">
              <PinchMeter
                label="Pinch — thumb + index"
                value={debug.pinchIndex}
                closeThreshold={debug.thresholds.pinchClose}
                openThreshold={debug.thresholds.pinchOpen}
              />
              <PinchMeter
                label="Pinch — thumb + middle"
                value={debug.pinchMiddle}
                closeThreshold={debug.thresholds.pinchClose}
                openThreshold={debug.thresholds.pinchOpen}
              />
            </div>

            <div>
              <p className="text-xs font-medium text-muted-foreground">
                Finger extension · curl angle at the middle joint
              </p>
              <ul className="mt-2 grid grid-cols-2 gap-1.5 sm:grid-cols-5">
                {FINGERS.map((finger) => {
                  const extended = debug.extended[finger]
                  return (
                    <li
                      key={finger}
                      className={cn(
                        'rounded-md border px-2 py-1.5',
                        extended
                          ? 'border-status-live/40 bg-status-live/10'
                          : 'border-border bg-secondary/40',
                      )}
                    >
                      <p
                        className={cn(
                          'text-xs font-medium',
                          extended ? 'text-status-live' : 'text-muted-foreground',
                        )}
                      >
                        {FINGER_LABELS[finger]}
                      </p>
                      <p className="tabular text-sm text-foreground">
                        {debug.angles[finger].toFixed(0)}°
                      </p>
                    </li>
                  )
                })}
              </ul>
            </div>

            <p className="text-xs text-muted-foreground">
              Hand scale <span className="tabular text-foreground">{debug.handScale.toFixed(3)}</span>{' '}
              — every distance above is a multiple of it, which is what keeps one threshold working
              across different cameras, lenses and distances from the lens.
            </p>
          </div>
        ) : (
          <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
            <Hand className="size-4" aria-hidden />
            No hand detected — nothing to measure.
          </div>
        )}
      </section>

      <section className="surface p-4">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Engine
        </h2>
        <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <Row label="Connection" value={phase} />
          <Row label="Camera" value={cameraName ?? camera} />
          {/* What the driver actually granted — capture requests are advisory and often ignored. */}
          <Row
            label="Capture resolution"
            value={frameWidth && frameHeight ? `${frameWidth} × ${frameHeight}` : '—'}
          />
          <Row label="Engine version" value={engineVersion ?? '—'} />
          <Row label="Protocol" value={serverProtocol ?? '—'} />
          <Row label="Endpoint" value={endpoint ?? '—'} />
          <Row label="Tracking" value={tracking} />
        </dl>
      </section>

      <section className="surface p-4">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Cursor actuation
        </h2>
        <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <Row label="Available" value={cursorAvailable ? 'yes' : 'no'} />
          <Row label="Active" value={cursorEnabled ? 'YES — driving the mouse' : 'no'} />
          <Row label="Mode" value={cursorDryRun ? 'dry run (logged only)' : 'live'} />
          <Row label="Emergency stop" value={killswitchHotkey ?? '—'} />
        </dl>
        {cursorReason ? (
          <p className="mt-3 text-xs text-muted-foreground">{cursorReason}</p>
        ) : null}
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
          Actuation is off at every launch and is never restored automatically — unlike tracking.
          It also disarms itself on pause, on kill-switch, and when the last client disconnects,
          so the value above is the engine's, not this page's memory of what was requested.
        </p>
      </section>
    </div>
  )
}

function Metric({
  icon: Icon,
  label,
  value,
  note,
}: {
  icon: typeof Gauge
  label: string
  value: string
  note: string
}) {
  return (
    <div className="surface p-3">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <Icon className="size-3.5" aria-hidden />
        <span className="text-xs font-medium tracking-wide uppercase">{label}</span>
      </div>
      <p className="tabular mt-2 text-xl font-semibold text-foreground">{value}</p>
      <p className="mt-0.5 text-[10px] text-muted-foreground">{note}</p>
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-border/60 pb-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate text-foreground">{value}</dd>
    </div>
  )
}
