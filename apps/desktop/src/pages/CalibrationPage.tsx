import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Check, Crosshair } from 'lucide-react'

import { CameraPreview } from '@/components/CameraPreview'
import { ActiveAreaOverlay } from '@/components/calibration/ActiveAreaOverlay'
import { CalibrationStepper } from '@/components/calibration/CalibrationStepper'
import { MeasureStep } from '@/components/calibration/MeasureStep'
import { SettingSlider } from '@/components/settings/SettingSlider'
import { Button } from '@/components/ui/button'
import { useEngineCommands } from '@/hooks/useEngine'
import { useNow } from '@/hooks/useNow'
import { shouldCalibrate, STEP_DEFINITIONS } from '@/lib/calibration'
import type { CalibrationStep, SettingsPatch } from '@/lib/protocol'
import { useCalibrationStore } from '@/stores/calibrationStore'
import { useConnectionStore } from '@/stores/connectionStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { pipelineIssue, useTrackingStore } from '@/stores/trackingStore'

/**
 * The order the wizard runs in, from `projectbrief.md`: resting position, reach, sensitivity,
 * click threshold.
 *
 * Sensitivity is the odd one out and stays a slider on purpose. The other three ask the hand a
 * question with a right answer; how much lag you will trade for steadiness is a preference, and
 * there is no measurement that reveals it.
 */
type PageStep = { kind: 'measure'; step: CalibrationStep } | { kind: 'sensitivity' }

const PAGE_STEPS: PageStep[] = [
  { kind: 'measure', step: 'neutral' },
  { kind: 'measure', step: 'reach' },
  { kind: 'sensitivity' },
  { kind: 'measure', step: 'pinch' },
]

const STEP_TITLES = ['Resting position', 'Reach', 'Sensitivity', 'Click threshold']

/**
 * The calibration wizard.
 *
 * ## Two rules shape this screen
 *
 * **It must be completable from the keyboard alone.** The user has no working gesture cursor until
 * it finishes — that is the whole point — so every control here is a real button or a native
 * range input, and nothing depends on hover or on dragging.
 *
 * **It never moves the real cursor.** The live preview draws the reach box and the hand's position
 * inside it from telemetry, which the engine sends whether or not actuation is on. Starting a
 * measurement disables actuation engine-side, because the user is about to sweep a hand across the
 * frame on purpose.
 */
export function CalibrationPage() {
  const navigate = useNavigate()
  const now = useNow()
  const [index, setIndex] = useState(0)
  const [seen, setSeen] = useState<Set<number>>(() => new Set([0]))
  /** Which steps have had their measurement accepted. Local: it is wizard progress, not engine
   * state — the engine has no concept of "the user pressed Apply on step 2". */
  const [applied, setApplied] = useState<Set<CalibrationStep>>(() => new Set())

  const commands = useEngineCommands()
  const connected = useConnectionStore((state) => state.phase === 'connected')

  const settings = useSettingsStore((state) => state.settings)
  const bounds = useSettingsStore((state) => state.bounds)
  const activeArea = useSettingsStore((state) => state.activeArea)
  const profile = useSettingsStore((state) => state.profile)
  const rejection = useSettingsStore((state) => state.lastRejection)

  const session = useCalibrationStore((state) => state.session)

  const camera = useTrackingStore((state) => state.camera)
  const tracking = useTrackingStore((state) => state.tracking)
  const handDetected = useTrackingStore((state) => state.handDetected)
  const lastSampleAt = useTrackingStore((state) => state.lastSampleAt)

  const issue = pipelineIssue(connected, { camera, tracking, handDetected, lastSampleAt }, now)
  // "No hand right now" is not a reason to refuse a measurement — the countdown is exactly the
  // window in which the user puts their hand up.
  const canMeasure = connected && tracking === 'running' && issue !== 'stream-stalled'
  const blockedReason = useMemo(() => {
    if (!connected) return 'The engine is not connected, so there are no frames to measure.'
    if (tracking !== 'running') return 'Start tracking before measuring — a measurement needs live frames.'
    if (issue === 'stream-stalled') return 'Connected, but no telemetry has arrived recently.'
    return null
  }, [connected, tracking, issue])

  const current = PAGE_STEPS[index]
  const isLast = index === PAGE_STEPS.length - 1

  const goTo = (next: number) => {
    const clamped = Math.max(0, Math.min(PAGE_STEPS.length - 1, next))
    setIndex(clamped)
    setSeen((previous) => new Set(previous).add(clamped))
  }

  const applySuggestion = (step: CalibrationStep, patch: SettingsPatch) => {
    commands.updateSettings(patch)
    setApplied((previous) => new Set(previous).add(step))
  }

  const [finishing, setFinishing] = useState(false)

  /**
   * Leave only once the engine has confirmed the wizard is finished.
   *
   * Navigating straight after sending `complete` is a race the gate wins: the profile does not
   * report `calibrated` until the engine's `settings` broadcast comes back, and until then
   * `CalibrationGate` would bounce us straight to this page again. With no profile at all
   * (`--no-profile`) the gate never applies, so this fires immediately.
   */
  useEffect(() => {
    if (finishing && !shouldCalibrate(profile)) navigate('/')
  }, [finishing, profile, navigate])

  const finish = () => {
    commands.completeCalibration()
    setFinishing(true)
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4">
      <div>
        <div className="flex items-center gap-2.5">
          <Crosshair className="size-5 text-muted-foreground" aria-hidden />
          <h1 className="text-xl font-semibold tracking-tight">Calibration</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Four steps, measured against your hand rather than assumed. Everything here is applied
          live and saved by the engine, so it survives a restart with the app closed.
        </p>
      </div>

      <CalibrationStepper
        titles={STEP_TITLES}
        current={index}
        completed={new Set([...seen].filter((value) => value < index))}
        onSelect={goTo}
      />

      {profile?.stale ? (
        <p className="surface border-status-degraded/40 p-3 text-xs text-muted-foreground">
          <span className="font-medium text-status-degraded">Saved profile not in use. </span>
          {profile.reason}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        {/* The preview keeps its own aspect box — the overlay rides inside it so the reach
            rectangle and the skeleton cannot disagree about where the frame is. */}
        <div className="min-h-[16rem]">
          <CameraPreview issue={issue}>
            <ActiveAreaOverlay area={activeArea} />
          </CameraPreview>
        </div>

        <div className="flex flex-col gap-3">
          {current.kind === 'measure' ? (
            <MeasureStep
              definition={STEP_DEFINITIONS[current.step]}
              session={session?.step === current.step ? session : null}
              canMeasure={canMeasure}
              blockedReason={blockedReason}
              applied={applied.has(current.step)}
              onMeasure={() => commands.startMeasurement(current.step)}
              onCancel={commands.cancelMeasurement}
              onApply={(patch) => applySuggestion(current.step, patch)}
            />
          ) : (
            <section className="surface flex flex-col gap-4 p-4">
              <div>
                <h2 className="text-sm font-semibold text-foreground">Sensitivity</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Move your hand and watch the marker. Adjust until it follows you without
                  trembling when you hold still.
                </p>
                <p className="mt-1 text-xs text-muted-foreground/80">
                  No measurement can settle this one — how much lag is worth how much steadiness is
                  a preference, not a fact about your hand.
                </p>
              </div>

              {settings && bounds ? (
                <>
                  {/* Both pointer sliders are phrased as "follow speed" so they point the same
                      way. Naming one of them by the quality it trades away — steadiness — made it
                      read backwards against its neighbour, because `minCutoff` rises as smoothing
                      falls. */}
                  <SettingSlider
                    label="Follow speed at rest"
                    lowLabel="steadier"
                    highLabel="faster"
                    description="Low values hold the cursor still while your hand is; too low and it feels sluggish."
                    value={settings.pointer.minCutoff}
                    bounds={bounds.pointer.minCutoff}
                    step={0.1}
                    decimals={1}
                    onCommit={(minCutoff) => commands.updateSettings({ pointer: { minCutoff } })}
                  />
                  <SettingSlider
                    label="Follow speed when moving"
                    lowLabel="smoother"
                    highLabel="faster"
                    description="How much the cursor may speed up once your hand is actually moving."
                    value={settings.pointer.beta}
                    bounds={bounds.pointer.beta}
                    step={0.5}
                    decimals={1}
                    onCommit={(beta) => commands.updateSettings({ pointer: { beta } })}
                  />
                  <SettingSlider
                    label="Active area"
                    lowLabel="less hand travel"
                    highLabel="more"
                    description="Share of the frame mapped to the whole screen — the dashed box in the preview."
                    value={settings.cursor.coverage}
                    bounds={bounds.cursor.coverage}
                    onCommit={(coverage) => commands.updateSettings({ cursor: { coverage } })}
                  />
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Waiting for the engine to report its settings.
                </p>
              )}
            </section>
          )}

          {rejection ? (
            <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {rejection}
            </p>
          ) : null}

          <div className="flex justify-between gap-2">
            <Button variant="ghost" onClick={() => goTo(index - 1)} disabled={index === 0}>
              <ArrowLeft aria-hidden />
              Back
            </Button>

            {isLast ? (
              <Button onClick={finish} disabled={finishing}>
                <Check aria-hidden />
                {finishing ? 'Saving…' : 'Finish calibration'}
              </Button>
            ) : (
              <Button onClick={() => goTo(index + 1)}>
                Next
                <ArrowRight aria-hidden />
              </Button>
            )}
          </div>

          <p className="text-[10px] leading-relaxed text-muted-foreground">
            Steps can be skipped and re-run — nothing is applied until you press Apply. Finishing
            records that you have been through the wizard; the settings themselves are already
            saved as you accept them.
          </p>
        </div>
      </div>
    </div>
  )
}
