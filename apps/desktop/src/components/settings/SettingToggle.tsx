import { cn } from '@/lib/utils'

interface SettingToggleProps {
  label: string
  description?: string
  /** The engine's current value. */
  value: boolean
  disabled?: boolean
  onCommit: (value: boolean) => void
}

/**
 * One boolean engine setting.
 *
 * Separate from `SettingSlider` rather than a mode of it: that control renders a range, and the
 * wire reports `null` bounds for booleans precisely because there is no range to render. Making it
 * cope with both would mean inventing a range for half its inputs.
 *
 * A native checkbox, not a styled div with a click handler — Space toggles it, Tab reaches it, and
 * screen readers announce its state without any ARIA of ours.
 *
 * Like every control here it sends a proposal. There is no local draft, because unlike a drag a
 * click has no intermediate state to show: the engine either accepts it and broadcasts, or refuses
 * and the box stays where it was.
 */
export function SettingToggle({
  label,
  description,
  value,
  disabled = false,
  onCommit,
}: SettingToggleProps) {
  return (
    <label className={cn('flex cursor-pointer items-start gap-3', disabled && 'opacity-50')}>
      <input
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 accent-status-live"
        checked={value}
        disabled={disabled}
        onChange={(event) => onCommit(event.target.checked)}
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium text-foreground">{label}</span>
        {description ? (
          <span className="mt-0.5 block text-[10px] leading-relaxed text-muted-foreground">
            {description}
          </span>
        ) : null}
      </span>
    </label>
  )
}
