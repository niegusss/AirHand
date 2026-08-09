import { motion } from 'framer-motion'
import { MousePointer2, ShieldAlert, ShieldOff, TriangleAlert } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface CursorControlProps {
  available: boolean
  enabled: boolean
  reason: string | null
  dryRun: boolean
  killswitchHotkey: string | null
  connected: boolean
  onEnable: () => void
  onDisable: () => void
  /** Single-column layout for the narrow app-shell rail. */
  compact?: boolean
}

/**
 * Arms and disarms OS cursor actuation.
 *
 * Three deliberate choices:
 *
 * - **Off on every launch.** Actuation is never restored automatically, unlike tracking. It has to
 *   be asked for each session.
 * - **`enabled` comes from the engine, never from local state.** The engine can disarm itself —
 *   kill-switch, pause, client disconnect — and a control that rendered the last *request* would
 *   claim the cursor is armed when it is not.
 * - **The emergency hotkey is shown while armed**, not hidden in documentation. If the pointer
 *   misbehaves, the user cannot click anything to stop it; the keyboard is the only way out, and
 *   it is useless if they do not know the combination.
 *
 * `compact` narrows the layout for the app-shell rail. It changes how things are stacked and
 * nothing else — the armed state and the hotkey stay just as loud, because a smaller card is not
 * a reason to whisper about the thing currently holding the mouse.
 */
export function CursorControl({
  available,
  enabled,
  reason,
  dryRun,
  killswitchHotkey,
  connected,
  onEnable,
  onDisable,
  compact = false,
}: CursorControlProps) {
  if (!available) {
    return (
      <div className="surface p-3">
        <div className="flex items-center gap-2 text-muted-foreground">
          <ShieldOff className="size-4 shrink-0" aria-hidden />
          <span className="text-sm font-medium">Cursor control unavailable</span>
        </div>
        {reason ? <p className="mt-2 text-xs text-muted-foreground">{reason}</p> : null}
      </div>
    )
  }

  return (
    <div
      className={cn(
        'transition-colors duration-200',
        // Armed, this panel stops being glass entirely — see `.surface-alert`. The thing holding
        // the mouse does not get to blend into the background.
        enabled ? 'surface-alert' : 'surface',
        compact ? 'p-3' : 'p-4',
      )}
    >
      <div
        className={cn(
          'flex gap-3',
          compact ? 'flex-col' : 'flex-wrap items-center justify-between',
        )}
      >
        <div className="flex items-center gap-2">
          <MousePointer2
            className={cn(
              'size-4 shrink-0',
              enabled ? 'text-status-degraded' : 'text-muted-foreground',
            )}
            aria-hidden
          />
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">
              {enabled ? 'Cursor control is ACTIVE' : 'Cursor control'}
            </p>
            {compact && !enabled ? null : (
              <p className="text-xs text-muted-foreground">
                {enabled
                  ? 'Gestures are driving the real mouse.'
                  : 'Off. Gestures are recognised but do not touch the system.'}
              </p>
            )}
          </div>
        </div>

        <div className={cn('flex items-center gap-2', compact && 'flex-wrap')}>
          {dryRun ? (
            <span className="rounded-md border border-border bg-secondary px-2 py-1 text-xs text-muted-foreground">
              dry run
            </span>
          ) : null}
          {enabled ? (
            <Button
              variant="destructive"
              size="sm"
              onClick={onDisable}
              disabled={!connected}
              className={cn(compact && 'flex-1')}
            >
              Disable
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={onEnable}
              disabled={!connected}
              className={cn(compact && 'flex-1')}
            >
              {compact ? 'Enable' : 'Enable cursor control'}
            </Button>
          )}
        </div>
      </div>

      {enabled ? (
        <motion.div
          initial={{ opacity: 0, y: -3 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18, ease: 'easeOut' }}
          className="mt-3 flex items-start gap-2 rounded-md border border-status-degraded/30 bg-background/40 p-2.5"
          role="alert"
        >
          <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-status-degraded" aria-hidden />
          <p className="text-xs leading-relaxed text-foreground">
            Emergency stop:{' '}
            <kbd className="rounded border border-border bg-secondary px-1.5 py-0.5 font-mono text-[11px]">
              {formatHotkey(killswitchHotkey)}
            </kbd>{' '}
            — works even if the pointer is unusable. Removing your hand from view stops movement
            and releases any held button.
          </p>
        </motion.div>
      ) : null}

      {!enabled && dryRun ? (
        <p className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Dry run: the engine will log cursor actions instead of performing them.
        </p>
      ) : null}
    </div>
  )
}

/** `<ctrl>+<alt>+<space>` reads badly on screen; show it the way a keyboard shortcut looks. */
function formatHotkey(hotkey: string | null): string {
  if (!hotkey) return 'not configured'
  return hotkey
    .split('+')
    .map((part) => {
      const name = part.replace(/[<>]/g, '')
      return name.charAt(0).toUpperCase() + name.slice(1)
    })
    .join(' + ')
}
