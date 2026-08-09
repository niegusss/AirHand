import { CursorControl } from '@/components/CursorControl'
import { StopControl } from '@/components/StopControl'
import { useEngineCommands } from '@/hooks/useEngine'
import { useConnectionStore } from '@/stores/connectionStore'
import { useTrackingStore } from '@/stores/trackingStore'

/**
 * Tracking and cursor controls, wired to the engine.
 *
 * Lives in the app shell rather than on the Dashboard so it is reachable from every screen. That
 * is a strengthening of the old rule, not a break with it: the way to stop a pipeline that is
 * driving real OS input must never be one navigation away.
 *
 * The two children stay presentational — they take state and callbacks — so they remain easy to
 * render in isolation. This is the only place that reads the stores for them.
 */
export function EngineControls() {
  const connected = useConnectionStore((state) => state.phase === 'connected')

  const tracking = useTrackingStore((state) => state.tracking)
  const cursorAvailable = useTrackingStore((state) => state.cursorAvailable)
  const cursorEnabled = useTrackingStore((state) => state.cursorEnabled)
  const cursorReason = useTrackingStore((state) => state.cursorReason)
  const cursorDryRun = useTrackingStore((state) => state.cursorDryRun)
  const killswitchHotkey = useTrackingStore((state) => state.killswitchHotkey)

  const commands = useEngineCommands()

  return (
    <div className="flex flex-col gap-3">
      <StopControl
        tracking={tracking}
        connected={connected}
        onStart={commands.start}
        onPause={commands.pause}
        onStop={commands.stop}
        stacked
      />

      <CursorControl
        available={cursorAvailable}
        enabled={cursorEnabled}
        reason={cursorReason}
        dryRun={cursorDryRun}
        killswitchHotkey={killswitchHotkey}
        connected={connected}
        onEnable={commands.enableCursor}
        onDisable={commands.disableCursor}
        compact
      />
    </div>
  )
}
