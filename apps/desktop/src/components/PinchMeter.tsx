import { cn } from '@/lib/utils'

interface PinchMeterProps {
  label: string
  /** Distance in multiples of hand scale — never pixels, so it is comparable across cameras. */
  value: number
  closeThreshold: number
  openThreshold: number
  /** Full-scale value for the bar. Distances above this are clamped. */
  max?: number
}

/**
 * Pinch distance against the engine's own hysteresis band.
 *
 * The point of drawing both thresholds is that the gap between them is the design: a single line
 * would make a hand resting on it look fine while it fires phantom clicks. Seeing the band makes
 * "is my hysteresis wide enough" a question you can answer by looking.
 */
export function PinchMeter({
  label,
  value,
  closeThreshold,
  openThreshold,
  max = 1.4,
}: PinchMeterProps) {
  const toPercent = (input: number) => `${Math.min(100, Math.max(0, (input / max) * 100))}%`

  const closed = value < closeThreshold
  const inBand = value >= closeThreshold && value <= openThreshold

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <span
          className={cn(
            'tabular text-sm font-semibold',
            closed ? 'text-status-live' : inBand ? 'text-status-degraded' : 'text-foreground',
          )}
        >
          {value.toFixed(2)}
        </span>
      </div>

      <div className="relative mt-2 h-2.5 overflow-hidden rounded-full bg-secondary">
        {/* Hysteresis band: between these two lines the state holds whatever it already was. */}
        <div
          className="absolute inset-y-0 bg-status-degraded/25"
          style={{ left: toPercent(closeThreshold), right: `calc(100% - ${toPercent(openThreshold)})` }}
        />
        <div
          className={cn(
            'absolute inset-y-0 left-0 rounded-full transition-[width] duration-75',
            closed ? 'bg-status-live' : 'bg-status-idle',
          )}
          style={{ width: toPercent(value) }}
        />
        <div
          className="absolute inset-y-0 w-px bg-status-live/70"
          style={{ left: toPercent(closeThreshold) }}
        />
        <div
          className="absolute inset-y-0 w-px bg-status-degraded/70"
          style={{ left: toPercent(openThreshold) }}
        />
      </div>

      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        <span className="tabular">close {closeThreshold.toFixed(2)}</span>
        <span className="tabular">open {openThreshold.toFixed(2)}</span>
      </div>
    </div>
  )
}
