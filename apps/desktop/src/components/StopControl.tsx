import { Pause, Play, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { TrackingState } from '@/lib/protocol'

interface StopControlProps {
  tracking: TrackingState
  connected: boolean
  onStart: () => void
  onPause: () => void
  onStop: () => void
  /** Full-width buttons in a column, for the narrow app-shell rail. */
  stacked?: boolean
}

/**
 * Start / pause / stop for the tracking pipeline.
 *
 * Rendered in the app shell, so it is present on every screen and never moves into Settings.
 * Tracking auto-starts on every launch after calibration, which means the pipeline is running
 * before the user has touched anything — the way out has to be immediately reachable no matter
 * which page they are on.
 */
export function StopControl({
  tracking,
  connected,
  onStart,
  onPause,
  onStop,
  stacked = false,
}: StopControlProps) {
  const running = tracking === 'running'

  return (
    <div className={cn('flex gap-2', stacked ? 'flex-col [&>button]:w-full' : 'items-center')}>
      {running ? (
        <Button variant="outline" size="sm" onClick={onPause} disabled={!connected}>
          <Pause className="size-3.5" aria-hidden />
          Pause
        </Button>
      ) : (
        <Button variant="outline" size="sm" onClick={onStart} disabled={!connected}>
          <Play className="size-3.5" aria-hidden />
          {tracking === 'paused' ? 'Resume' : 'Start'}
        </Button>
      )}

      <Button
        variant="destructive"
        size="sm"
        onClick={onStop}
        disabled={!connected || tracking === 'idle'}
        // Tactile press feedback, per the design conventions in systemPatterns.md.
        className={cn('active:scale-[0.97] transition-transform duration-75')}
      >
        <Square className="size-3.5" aria-hidden />
        Stop tracking
      </Button>
    </div>
  )
}
