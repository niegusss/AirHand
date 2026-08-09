import { ArrowUpDown, Hand, MousePointer2, MousePointerClick, Move, Pointer } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { cn } from '@/lib/utils'
import { gestureLabel, type Gesture } from '@/lib/protocol'

const GESTURES: Gesture[] = ['move', 'left_click', 'right_click', 'drag', 'scroll']

const GESTURE_ICONS: Record<Gesture, LucideIcon> = {
  none: Hand,
  move: MousePointer2,
  left_click: MousePointerClick,
  right_click: Pointer,
  drag: Move,
  scroll: ArrowUpDown,
}

interface GestureIndicatorProps {
  gesture: Gesture
  active: boolean
}

/**
 * Shows every recognizable gesture at once, highlighting the current one.
 *
 * Showing only the active gesture would leave the user guessing what the system can even do —
 * which matters while the Gesture Engine's thresholds are still being tuned.
 */
export function GestureIndicator({ gesture, active }: GestureIndicatorProps) {
  return (
    <div className="surface p-4">
      <div className="flex items-center gap-2 text-muted-foreground">
        <Hand className="size-3.5" aria-hidden />
        <span className="text-xs font-medium tracking-wide uppercase">Gesture</span>
      </div>

      <p
        className={cn(
          'mt-3 text-2xl font-semibold',
          active && gesture !== 'none' ? 'text-status-live' : 'text-muted-foreground',
        )}
        aria-live="polite"
      >
        {active ? gestureLabel(gesture) : '—'}
      </p>

      <ul className="mt-4 flex flex-wrap gap-1.5">
        {GESTURES.map((candidate) => {
          const Icon = GESTURE_ICONS[candidate]
          const isCurrent = active && candidate === gesture
          return (
            <li key={candidate}>
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors duration-150',
                  isCurrent
                    ? 'border-status-live/40 bg-status-live/10 text-status-live'
                    : 'border-border bg-secondary/40 text-muted-foreground',
                )}
              >
                <Icon className="size-3" aria-hidden />
                {gestureLabel(candidate)}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
