import { useState } from 'react'
import { Link } from 'react-router-dom'
import { RotateCcw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { ProfileInfo } from '@/lib/protocol'

/**
 * The engine's ISO timestamp, in the reader's own locale.
 *
 * Parsed rather than printed raw: the engine writes an offset so the instant is unambiguous, and
 * `toLocaleString` is what turns that back into the wall clock the user was looking at. A string
 * that fails to parse is shown as it arrived — it is still the truth about the file, and hiding it
 * would lose the only clue to why it is malformed.
 */
function formatSavedAt(savedAt: string | null): string {
  if (savedAt === null) return 'not recorded'
  const at = new Date(savedAt)
  return Number.isNaN(at.getTime()) ? savedAt : at.toLocaleString()
}

interface ProfileCardProps {
  profile: ProfileInfo | null
  disabled?: boolean
  onReset: () => void
}

/**
 * Where the settings are stored, and the one way to put them all back.
 *
 * ## The reset needs its consequences written down, not discovered
 *
 * `set_settings {reset: true}` restores the engine's built-in defaults for *everything* — including
 * the resting centre and the pinch threshold the calibration wizard measured against this user's
 * hand. There is no per-section reset: the engine only knows the whole-value form, and inventing a
 * partial one here would mean a second write path with its own validation.
 *
 * It also does not clear `profile.calibrated`, so the first-run gate will not fire and nobody is
 * sent back through the wizard. That is deliberate — the user has been through it — but it means
 * the only thing standing between them and unmeasured thresholds is this text and the confirmation
 * step below it.
 */
export function ProfileCard({ profile, disabled, onReset }: ProfileCardProps) {
  const [confirming, setConfirming] = useState(false)

  return (
    <section className="surface flex flex-col gap-3 p-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">Saved profile</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Everything on this screen is applied the moment the engine accepts it and written to disk
          straight away — there is nothing here to save.
        </p>
      </div>

      {profile === null ? (
        <p className="text-xs text-muted-foreground">
          The engine was started with <code className="tabular">--no-profile</code>.{' '}
          <span className="text-foreground/70">Nothing is being persisted</span> — every change
          lasts until the engine stops.
        </p>
      ) : (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted-foreground">File</dt>
          <dd className="tabular truncate text-foreground/80" title={profile.path}>
            {profile.path}
          </dd>
          <dt className="text-muted-foreground">Loaded</dt>
          <dd className="text-foreground/80">{profile.loaded ? 'yes' : 'no — using defaults'}</dd>
          <dt className="text-muted-foreground">Calibrated</dt>
          <dd className="text-foreground/80">
            {profile.calibrated ? 'yes' : 'no — the wizard has not been completed'}
          </dd>
          <dt className="text-muted-foreground">Last saved</dt>
          <dd className="text-foreground/80">{formatSavedAt(profile.savedAt)}</dd>
        </dl>
      )}

      {profile?.stale ? (
        <p className="rounded-md border border-status-degraded/40 px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium text-status-degraded">Saved profile not in use. </span>
          {profile.reason}
        </p>
      ) : null}

      <div className="flex flex-col gap-2 border-t border-white/5 pt-3">
        {confirming ? (
          <>
            <p className="text-xs text-muted-foreground">
              This restores every value above, including the resting centre and click threshold the
              wizard <span className="text-foreground/80">measured against your hand</span>. You
              will not be sent back through calibration — run it from{' '}
              <Link to="/calibration" className="text-foreground underline underline-offset-2">
                Calibration
              </Link>{' '}
              when you want those numbers back.
            </p>
            <div className="flex gap-2">
              <Button variant="destructive" onClick={onReset} disabled={disabled}>
                Restore defaults
              </Button>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </>
        ) : (
          <Button variant="ghost" className="self-start" onClick={() => setConfirming(true)} disabled={disabled}>
            <RotateCcw aria-hidden />
            Restore engine defaults
          </Button>
        )}
      </div>
    </section>
  )
}
