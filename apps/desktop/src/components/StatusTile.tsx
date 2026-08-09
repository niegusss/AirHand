import type { LucideIcon } from 'lucide-react'

import { cn } from '@/lib/utils'

export type Tone = 'live' | 'degraded' | 'down' | 'idle'

const TONE_STYLES: Record<Tone, { dot: string; value: string; ring: string }> = {
  live: {
    dot: 'bg-status-live',
    value: 'text-status-live',
    ring: 'ring-status-live/25',
  },
  degraded: {
    dot: 'bg-status-degraded',
    value: 'text-status-degraded',
    ring: 'ring-status-degraded/25',
  },
  down: {
    dot: 'bg-status-down',
    value: 'text-status-down',
    ring: 'ring-status-down/25',
  },
  idle: {
    dot: 'bg-status-idle',
    value: 'text-foreground',
    ring: 'ring-transparent',
  },
}

interface StatusTileProps {
  label: string
  value: string
  /** Secondary line — units, device name, or why the value is what it is. */
  hint?: string | null
  tone?: Tone
  icon: LucideIcon
  /** Renders the value with tabular figures so it does not reflow as digits change. */
  numeric?: boolean
  /** Tighter padding and type, for the Dashboard's readout strip. */
  dense?: boolean
}

export function StatusTile({
  label,
  value,
  hint,
  tone = 'idle',
  icon: Icon,
  numeric = false,
  dense = false,
}: StatusTileProps) {
  const styles = TONE_STYLES[tone]

  return (
    <div
      className={cn(
        'surface ring-1 ring-inset transition-colors duration-200',
        dense ? 'p-3' : 'p-4',
        styles.ring,
      )}
    >
      <div className="flex items-center gap-2 text-muted-foreground">
        <Icon className="size-3.5 shrink-0" aria-hidden />
        <span className="truncate text-xs font-medium tracking-wide uppercase">{label}</span>
        <span className={cn('ml-auto size-1.5 shrink-0 rounded-full', styles.dot)} aria-hidden />
      </div>

      <p
        className={cn(
          'font-semibold',
          dense ? 'mt-1.5 text-xl' : 'mt-3 text-2xl',
          styles.value,
          numeric && 'tabular',
        )}
      >
        {value}
      </p>

      {hint ? (
        <p className={cn('truncate text-xs text-muted-foreground', dense ? 'mt-0.5' : 'mt-1')}>
          {hint}
        </p>
      ) : null}
    </div>
  )
}
