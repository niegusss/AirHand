import { Check } from 'lucide-react'

import { cn } from '@/lib/utils'

interface CalibrationStepperProps {
  titles: string[]
  current: number
  /** Indices the user has been through. Drawn as done, and reachable again. */
  completed: ReadonlySet<number>
  onSelect: (index: number) => void
}

/**
 * Where you are in the wizard, and a way back to any step you have already seen.
 *
 * Real buttons, not decoration: every screen has to be operable from the keyboard, and this is
 * the user's only route back to re-run a measurement they were not happy with. Steps ahead of the
 * current one are disabled rather than hidden — the shape of what is coming is part of knowing how
 * long this will take.
 */
export function CalibrationStepper({
  titles,
  current,
  completed,
  onSelect,
}: CalibrationStepperProps) {
  return (
    <ol className="flex flex-wrap gap-1.5" aria-label="Calibration steps">
      {titles.map((title, index) => {
        const isCurrent = index === current
        const isDone = completed.has(index)
        const reachable = isDone || index <= current

        return (
          <li key={title}>
            <button
              type="button"
              onClick={() => onSelect(index)}
              disabled={!reachable}
              aria-current={isCurrent ? 'step' : undefined}
              className={cn(
                'flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors duration-150',
                'focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
                isCurrent
                  ? 'bg-secondary text-foreground'
                  : 'text-muted-foreground hover:bg-secondary/50 hover:text-foreground',
                !reachable && 'pointer-events-none opacity-40',
              )}
            >
              <span
                className={cn(
                  'flex size-4 items-center justify-center rounded-full text-[10px]',
                  isDone
                    ? 'bg-status-live text-background'
                    : isCurrent
                      ? 'bg-foreground text-background'
                      : 'bg-secondary text-muted-foreground',
                )}
                aria-hidden
              >
                {isDone ? <Check className="size-2.5" /> : index + 1}
              </span>
              {title}
            </button>
          </li>
        )
      })}
    </ol>
  )
}
