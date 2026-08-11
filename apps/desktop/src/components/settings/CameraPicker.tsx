import { Link } from 'react-router-dom'
import { RefreshCw, Video } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { CameraDevice } from '@/lib/protocol'

interface CameraPickerProps {
  devices: CameraDevice[]
  /** What the engine will open next. Null when the user has never chosen. */
  selected: number | null
  /** What it currently has open. Null before a camera is up — see `trackingStore.cameraIndex`. */
  active: number | null
  scanning: boolean
  /** Whether a scan has finished on this connection, so "none found" can be said honestly. */
  scanned: boolean
  reason: string | null
  disabled?: boolean
  onScan: () => void
  onSelect: (index: number) => void
}

/**
 * Which webcam the engine opens.
 *
 * ## Native radios, not a custom listbox
 *
 * A `fieldset` of `input[type=radio]` gets arrow-key navigation, the roving tab stop, the group
 * label and the announced position-in-set from the platform. Accessibility users are a named
 * audience in `projectbrief.md` and the UI must not require a mouse — and there is a sharper
 * version of that here than anywhere else in the app: **this is the screen someone reaches when
 * their camera is not working**, which is to say when gesture control cannot help them navigate it.
 *
 * ## Scanning is manual, always
 *
 * Probing opens and releases every device in turn, and the engine has to stop the pipeline to do
 * it — an open camera cannot be enumerated on Windows. A component that scanned on mount would
 * blink the camera on every visit to this screen. So the list starts empty and says so.
 *
 * ## Two indices, deliberately both shown
 *
 * `selected` is what the engine opens next; `active` is what it has open. They differ while the
 * pipeline is stopped, and they differ *permanently* after a device fails to open — which is
 * exactly when the user needs to see that the thing they picked is not the thing that is running.
 */
export function CameraPicker({
  devices,
  selected,
  active,
  scanning,
  scanned,
  reason,
  disabled,
  onScan,
  onSelect,
}: CameraPickerProps) {
  const busy = disabled || scanning

  /**
   * Which radio to check.
   *
   * `selected` is null until the user has chosen a device, and on a fresh profile that is the
   * normal state — while a camera is nevertheless open and running. Rendering nothing as checked
   * there is technically true and reads as broken: the feed is live and the list claims no device
   * is picked. So an unchosen selection falls back to whatever is actually open.
   *
   * The engine still knows the difference, and it still matters there — null is what keeps "never
   * chosen" out of the profile. It just is not a distinction worth drawing in a radio group.
   */
  const checked = selected ?? active

  return (
    <section className="surface flex flex-col gap-3 p-4">
      <div>
        <div className="flex items-center gap-2">
          <Video className="size-4 text-muted-foreground" aria-hidden />
          <h2 className="text-sm font-semibold text-foreground">Camera</h2>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Switching device reopens the pipeline. Cursor control stays off afterwards — it is
          requested per session and never restored on its own.
        </p>
      </div>

      {devices.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          {scanning
            ? 'Scanning — the pipeline is stopped while devices are probed.'
            : scanned
              ? 'No cameras found.'
              : 'No devices listed yet. Scanning releases and reopens the camera, so it only happens when you ask.'}
        </p>
      ) : (
        <fieldset className="flex flex-col gap-1" disabled={busy}>
          <legend className="sr-only">Capture device</legend>
          {devices.map((device) => (
            <label
              key={device.index}
              className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 text-xs hover:bg-white/5 has-checked:bg-white/5 has-disabled:cursor-default has-disabled:opacity-60 focus-within:outline focus-within:outline-2 focus-within:outline-ring"
            >
              <input
                type="radio"
                name="airhand-camera"
                className="size-3.5 accent-status-live"
                value={device.index}
                checked={checked === device.index}
                onChange={() => onSelect(device.index)}
              />
              <span className="flex-1 truncate text-foreground/90">{device.name}</span>
              <span className="tabular text-muted-foreground">
                {device.width}&times;{device.height}
              </span>
              {active === device.index ? (
                <span className="text-status-live" title="Currently open">
                  open
                </span>
              ) : null}
            </label>
          ))}
        </fieldset>
      )}

      {/* Only worth saying once a device is actually open on something other than the choice —
          before that the two agree and the note would be noise. */}
      {selected !== null && active !== null && selected !== active ? (
        <p className="rounded-md border border-status-degraded/40 px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium text-status-degraded">Not the device in use. </span>
          Camera {active} is open. Stop and start tracking to switch to your choice.
        </p>
      ) : null}

      {reason ? (
        <p className="rounded-md border border-status-degraded/40 px-3 py-2 text-xs text-muted-foreground">
          {reason}
        </p>
      ) : null}

      <div className="flex flex-col gap-2 border-t border-white/5 pt-3">
        <Button variant="ghost" className="self-start" onClick={onScan} disabled={busy}>
          <RefreshCw aria-hidden className={scanning ? 'animate-spin' : undefined} />
          {scanning ? 'Scanning…' : 'Scan for cameras'}
        </Button>
        <p className="text-xs text-muted-foreground">
          Click thresholds are measured in multiples of hand scale and survive a camera swap, but
          the resting centre and active area are measured against a particular frame. After
          changing device, re-run{' '}
          <Link to="/calibration" className="text-foreground underline underline-offset-2">
            Calibration
          </Link>{' '}
          if pointing feels off-centre.
        </p>
      </div>
    </section>
  )
}
