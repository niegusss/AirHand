import { Settings as SettingsIcon } from 'lucide-react'

import { CameraPreview } from '@/components/CameraPreview'
import { ActiveAreaOverlay } from '@/components/calibration/ActiveAreaOverlay'
import { PinchThresholds } from '@/components/settings/PinchThresholds'
import { ProfileCard } from '@/components/settings/ProfileCard'
import { SettingsSection } from '@/components/settings/SettingsSection'
import { useEngineCommands } from '@/hooks/useEngine'
import { useNow } from '@/hooks/useNow'
import { SETTINGS_GROUPS } from '@/lib/settings'
import { useConnectionStore } from '@/stores/connectionStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { pipelineIssue, useTrackingStore } from '@/stores/trackingStore'

/** The group whose values are unreadable as numbers — it gets the camera image beside it. */
const PREVIEWED_GROUP = 'Reach'
/** The group the pinch pair belongs to; see `PinchThresholds` for why it is not two knobs. */
const PINCH_GROUP = 'Clicking'

/**
 * Every knob the engine will accept, in one place.
 *
 * ## Nothing here is applied locally
 *
 * A control sends a proposal and waits. The engine validates it, applies it to the running
 * pipeline, writes it to the profile and broadcasts the result; only then does a slider move. That
 * is why there is no Save button and no dirty state — there is nothing held back to save, and a
 * value shown here is one the engine is actually using.
 *
 * ## The screen is built from a declaration, not written out
 *
 * `SETTINGS_GROUPS` lists which knob goes where; ranges and defaults arrive on every `settings`
 * message. Neither list is duplicated here, and a test fails if a knob reaches the wire without a
 * control — the failure mode this screen exists to close is a setting that is adjustable in the
 * engine and invisible in the app.
 */
export function SettingsPage() {
  const now = useNow()
  const commands = useEngineCommands()
  const connected = useConnectionStore((state) => state.phase === 'connected')

  const settings = useSettingsStore((state) => state.settings)
  const bounds = useSettingsStore((state) => state.bounds)
  const activeArea = useSettingsStore((state) => state.activeArea)
  const profile = useSettingsStore((state) => state.profile)
  const rejection = useSettingsStore((state) => state.lastRejection)

  const camera = useTrackingStore((state) => state.camera)
  const tracking = useTrackingStore((state) => state.tracking)
  const handDetected = useTrackingStore((state) => state.handDetected)
  const lastSampleAt = useTrackingStore((state) => state.lastSampleAt)

  const issue = pipelineIssue(connected, { camera, tracking, handDetected, lastSampleAt }, now)

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4">
      <div>
        <div className="flex items-center gap-2.5">
          <SettingsIcon className="size-5 text-muted-foreground" aria-hidden />
          <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Applied live and saved by the engine as you change them. Every range shown comes from the
          engine itself, so nothing here can offer a value it would refuse.
        </p>
      </div>

      {!connected ? (
        <p className="surface p-3 text-xs text-muted-foreground">
          <span className="font-medium text-status-degraded">Engine not connected. </span>
          Settings are owned by the engine, so there is nothing to read or change until it is
          reachable.
        </p>
      ) : null}

      {rejection ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {rejection}
        </p>
      ) : null}

      {settings === null || bounds === null ? (
        <p className="surface p-4 text-sm text-muted-foreground">
          Waiting for the engine to report its settings.
        </p>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-2">
          {SETTINGS_GROUPS.map((group) => (
            <div key={group.title} className="flex flex-col gap-4">
              <SettingsSection
                group={group}
                settings={settings}
                bounds={bounds}
                disabled={!connected}
                onCommit={commands.updateSettings}
              >
                {group.title === PINCH_GROUP ? (
                  <PinchThresholds
                    settings={settings}
                    bounds={bounds}
                    disabled={!connected}
                    onCommit={commands.updateSettings}
                  />
                ) : null}
              </SettingsSection>

              {/* The reach numbers are three fractions of a frame nobody can picture. The same
                  preview and overlay the wizard uses turns them into a rectangle. */}
              {group.title === PREVIEWED_GROUP ? (
                <div className="min-h-[14rem]">
                  <CameraPreview issue={issue}>
                    <ActiveAreaOverlay area={activeArea} />
                  </CameraPreview>
                </div>
              ) : null}
            </div>
          ))}

          <ProfileCard profile={profile} disabled={!connected} onReset={commands.resetSettings} />
        </div>
      )}
    </div>
  )
}
