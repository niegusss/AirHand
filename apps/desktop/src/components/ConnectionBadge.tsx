import { motion } from 'framer-motion'
import { Loader2, PlugZap, TriangleAlert, Unplug } from 'lucide-react'

import { cn } from '@/lib/utils'
import { useConnectionStore, type ConnectionPhase } from '@/stores/connectionStore'

const PHASE_COPY: Record<ConnectionPhase, { label: string; className: string; busy: boolean }> = {
  idle: { label: 'Idle', className: 'text-status-idle', busy: false },
  starting: { label: 'Starting engine', className: 'text-status-degraded', busy: true },
  connecting: { label: 'Connecting', className: 'text-status-degraded', busy: true },
  connected: { label: 'Engine connected', className: 'text-status-live', busy: false },
  reconnecting: { label: 'Reconnecting', className: 'text-status-degraded', busy: true },
  disconnected: { label: 'Disconnected', className: 'text-status-down', busy: false },
  error: { label: 'Engine unavailable', className: 'text-status-down', busy: false },
}

export function ConnectionBadge() {
  const phase = useConnectionStore((state) => state.phase)
  const engineVersion = useConnectionStore((state) => state.engineVersion)
  const attempt = useConnectionStore((state) => state.attempt)

  const copy = PHASE_COPY[phase]
  const Icon = phase === 'connected' ? PlugZap : phase === 'error' ? TriangleAlert : Unplug

  return (
    <motion.div
      // Animate the state change, not the live values behind it.
      key={phase}
      initial={{ opacity: 0, y: -2 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: 'easeOut' }}
      className={cn(
        'flex flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg border border-border bg-secondary/40 px-2.5 py-1.5 text-xs font-medium',
        copy.className,
      )}
      role="status"
      aria-live="polite"
    >
      {copy.busy ? (
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
      ) : (
        <Icon className="size-3.5" aria-hidden />
      )}
      <span>{copy.label}</span>
      {phase === 'connected' && engineVersion ? (
        <span className="text-muted-foreground">v{engineVersion}</span>
      ) : null}
      {phase === 'reconnecting' && attempt > 0 ? (
        <span className="text-muted-foreground tabular">attempt {attempt}</span>
      ) : null}
    </motion.div>
  )
}
