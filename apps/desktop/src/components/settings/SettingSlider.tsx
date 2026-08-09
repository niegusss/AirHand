import { useEffect, useState } from 'react'

import { cn } from '@/lib/utils'

interface SettingSliderProps {
  label: string
  /**
   * What each end of the range means, in the user's terms.
   *
   * Not decoration. These knobs are engine internals — `minCutoff` rises as smoothing *falls* — so
   * a label naming the quality can point the opposite way to the slider under it. Two such
   * controls side by side, each inverted differently, is unusable. Naming both ends makes the
   * direction impossible to misread whatever the underlying number does.
   */
  lowLabel?: string
  highLabel?: string
  description?: string
  /** The engine's current value. */
  value: number
  /**
   * `[low, high]`, straight off the wire — never hardcoded here.
   *
   * Nullable because the wire reports `null` for boolean knobs. Rendering a slider anyway would
   * mean inventing a range, which is the exact drift this control exists to avoid.
   */
  bounds: [number, number] | null
  step?: number
  decimals?: number
  disabled?: boolean
  onCommit: (value: number) => void
}

/**
 * One numeric engine setting, bounded by what the engine says it accepts.
 *
 * Deliberately generic: the Settings screen needs exactly this control, and writing it twice would
 * be two places for the bounds handling to drift.
 *
 * ## Why there is a local draft
 *
 * `settingsStore` holds no optimistic state — the engine is authoritative and a refused patch must
 * not leave a slider sitting where the engine refused to put it. That rule is about *committed*
 * values. While the user is still dragging, the knob has to follow the thumb or the control feels
 * broken, so the in-progress input lives here and is sent on release.
 *
 * That distinction is the point: a draft is what the user is about to ask for, not a guess at what
 * the engine did. If the engine refuses, `value` never changes, the draft stays visible next to the
 * rejection, and the user can see exactly what was turned down.
 *
 * Committing on release rather than on every input event also keeps a drag from writing the
 * profile to disk sixty times a second.
 */
export function SettingSlider({
  label,
  lowLabel,
  highLabel,
  description,
  value,
  bounds,
  step = 0.01,
  decimals = 2,
  disabled = false,
  onCommit,
}: SettingSliderProps) {
  const [draft, setDraft] = useState<number | null>(null)

  // The engine answered: its value is the truth again.
  useEffect(() => setDraft(null), [value])

  if (bounds === null) {
    return (
      <p className="text-sm text-muted-foreground">
        <span className="font-medium text-foreground">{label}</span> — the engine reported no range
        for this setting, so there is nothing safe to draw.
      </p>
    )
  }

  const [low, high] = bounds
  const shown = draft ?? value
  const commit = () => {
    if (draft !== null && draft !== value) onCommit(draft)
  }

  return (
    <label className={cn('block', disabled && 'opacity-50')}>
      <span className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <span className="tabular text-sm text-muted-foreground">{shown.toFixed(decimals)}</span>
      </span>

      <input
        type="range"
        className="mt-2 w-full accent-status-live"
        min={low}
        max={high}
        step={step}
        value={shown}
        disabled={disabled}
        onChange={(event) => setDraft(Number(event.target.value))}
        // Both, because the wizard has to be completable without a mouse: arrow keys fire
        // `change` and settle on `keyup`, a drag settles on `pointerup`, and `blur` catches the
        // case where focus leaves mid-interaction.
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />

      <span className="mt-1 flex justify-between gap-3 text-[10px] text-muted-foreground">
        <span>
          {lowLabel ? <span className="text-foreground/70">← {lowLabel} </span> : null}
          <span className="tabular">{low.toFixed(decimals)}</span>
        </span>
        <span className="text-right">
          <span className="tabular">{high.toFixed(decimals)}</span>
          {highLabel ? <span className="text-foreground/70"> {highLabel} →</span> : null}
        </span>
      </span>

      {description ? (
        <span className="mt-0.5 block text-[10px] text-muted-foreground/80">{description}</span>
      ) : null}
    </label>
  )
}
