import { CircleAlert, Play, RotateCcw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { describeSuggestion, type StepDefinition } from '@/lib/calibration'
import type { CalibrationMessage, SettingsPatch } from '@/lib/protocol'

interface MeasureStepProps {
  definition: StepDefinition
  /** The engine's session, already filtered to this step. Null if none has run here. */
  session: CalibrationMessage | null
  /** False when there is nothing to measure — disconnected, or tracking stopped. */
  canMeasure: boolean
  blockedReason: string | null
  /** True once this step's suggestion has been sent to the engine. */
  applied: boolean
  onMeasure: () => void
  onCancel: () => void
  onApply: (patch: SettingsPatch) => void
}

/**
 * One measured step: an instruction, a countdown, and a number to accept or reject.
 *
 * The measurement is never applied on the user's behalf. The engine's derivations are careful, but
 * they are still derived from a few seconds of one hand — offering the result and letting the user
 * accept it keeps a bad measurement from silently becoming the setting that makes clicking stop
 * working.
 */
export function MeasureStep({
  definition,
  session,
  canMeasure,
  blockedReason,
  applied,
  onMeasure,
  onCancel,
  onApply,
}: MeasureStepProps) {
  const sampling = session?.state === 'sampling'
  // The engine sends the total alongside the countdown precisely so this line does not need a
  // second copy of the step durations.
  const progress =
    sampling && session.secondsTotal > 0
      ? 1 - session.secondsRemaining / session.secondsTotal
      : 0

  return (
    <section className="surface flex flex-col gap-4 p-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">{definition.title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{definition.instruction}</p>
        <p className="mt-1 text-xs text-muted-foreground/80">{definition.purpose}</p>
      </div>

      {blockedReason ? (
        <p className="flex items-start gap-2 rounded-md border border-border bg-secondary/40 px-3 py-2 text-xs text-muted-foreground">
          <CircleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
          {blockedReason}
        </p>
      ) : null}

      {sampling ? (
        <div>
          <div className="flex items-baseline justify-between">
            <span className="text-xs font-medium text-status-live">Measuring…</span>
            <span className="tabular text-sm text-foreground">
              {session.secondsRemaining.toFixed(1)} s
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-secondary">
            <div
              className="h-full rounded-full bg-status-live transition-[width] duration-200"
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
          <p className="mt-1 text-[10px] text-muted-foreground">{session.samples} frames captured</p>
        </div>
      ) : null}

      {session?.state === 'failed' ? (
        <div className="rounded-md border border-status-degraded/40 bg-status-degraded/10 px-3 py-2">
          <p className="text-xs font-medium text-status-degraded">Measurement refused</p>
          <p className="mt-1 text-xs text-muted-foreground">{session.reason}</p>
          {session.measurement ? <Measurement values={session.measurement} /> : null}
        </div>
      ) : null}

      {session?.state === 'done' && session.suggestion ? (
        <div className="rounded-md border border-status-live/40 bg-status-live/10 px-3 py-2">
          <p className="text-xs font-medium text-status-live">
            {applied ? 'Applied' : 'Measured — apply this?'}
          </p>
          <ul className="mt-1.5 space-y-0.5">
            {describeSuggestion(session.suggestion).map((line) => (
              <li key={line} className="tabular text-xs text-foreground">
                {line}
              </li>
            ))}
          </ul>
          {session.measurement ? <Measurement values={session.measurement} /> : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {sampling ? (
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        ) : (
          <Button onClick={onMeasure} disabled={!canMeasure}>
            {session ? <RotateCcw aria-hidden /> : <Play aria-hidden />}
            {session ? 'Measure again' : 'Measure'}
          </Button>
        )}

        {session?.state === 'done' && session.suggestion && !applied ? (
          <Button variant="secondary" onClick={() => onApply(session.suggestion as SettingsPatch)}>
            Apply
          </Button>
        ) : null}
      </div>
    </section>
  )
}

/** The raw numbers behind the verdict — shown so a refusal is explicable, not just a wall. */
function Measurement({ values }: { values: Record<string, number | null> }) {
  const entries = Object.entries(values).filter(([, value]) => value !== null)
  if (entries.length === 0) return null

  return (
    <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-0.5 border-t border-border/60 pt-1.5">
      {entries.map(([key, value]) => (
        <div key={key} className="flex gap-1.5 text-[10px]">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="tabular text-foreground">{(value as number).toFixed(2)}</dd>
        </div>
      ))}
    </dl>
  )
}
