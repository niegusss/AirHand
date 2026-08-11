import { useCallback, useEffect } from 'react'

import { engineClient } from '@/lib/engineClient'
import type { CalibrationStep, SettingsPatch } from '@/lib/protocol'
import { startPump, stopPump } from '@/lib/telemetryPump'
import { useConnectionStore } from '@/stores/connectionStore'
import { useTrackingStore } from '@/stores/trackingStore'

/**
 * Owns the engine connection and the telemetry pump for the lifetime of the app.
 *
 * Mount exactly once, at the app root — mounting it per page would tear down and re-establish
 * the connection on every navigation.
 */
export function useEngineLifecycle(): void {
  useEffect(() => {
    engineClient.connect()
    startPump()

    return () => {
      stopPump()
      engineClient.disconnect()
    }
  }, [])
}

export interface EngineCommands {
  start: () => void
  stop: () => void
  pause: () => void
  enableCursor: () => void
  disableCursor: () => void
  /**
   * Propose a settings change. Nothing is applied locally — the engine validates, and the outcome
   * arrives either as a `settings` broadcast or as an `invalid_settings` error.
   */
  updateSettings: (patch: SettingsPatch) => void
  /** Restore the engine's built-in defaults. */
  resetSettings: () => void
  /**
   * Ask the engine to measure one calibration step. Refused unless tracking is running, and it
   * disables cursor actuation for the duration.
   */
  startMeasurement: (step: CalibrationStep) => void
  cancelMeasurement: () => void
  /** Record that the wizard was finished. Changes no setting — it only marks the profile. */
  completeCalibration: () => void
  /**
   * Scan for capture devices.
   *
   * Takes the pipeline down and puts it back — an open camera cannot be enumerated on Windows.
   * Deliberately manual, and never called on mount: probing opens hardware, and a component that
   * scanned when it rendered would blink the camera on every visit to this screen.
   */
  discoverCameras: () => void
  /** Choose a capture device. A proposal like everything else here; the engine decides. */
  selectCamera: (index: number) => void
  /**
   * Run discovery again, which in the desktop shell also means starting the engine if it is not
   * running. Deliberately manual: an automatic loop would relaunch a process that opens a camera,
   * every few seconds, for as long as it keeps failing.
   */
  reconnect: () => void
}

export function useEngineCommands(): EngineCommands {
  const start = useCallback(() => {
    engineClient.send({ type: 'command', action: 'start' })
  }, [])

  const stop = useCallback(() => {
    engineClient.send({ type: 'command', action: 'stop' })
  }, [])

  const pause = useCallback(() => {
    engineClient.send({ type: 'command', action: 'pause' })
  }, [])

  // Requests only. The engine decides, and reports the result in `status.cursorEnabled` — it can
  // refuse, and it can disable actuation on its own at any moment.
  const enableCursor = useCallback(() => {
    engineClient.send({ type: 'command', action: 'enable_cursor' })
  }, [])

  const disableCursor = useCallback(() => {
    engineClient.send({ type: 'command', action: 'disable_cursor' })
  }, [])

  // Same shape as the cursor commands, and for the same reason: a proposal, not a local edit.
  const updateSettings = useCallback((patch: SettingsPatch) => {
    engineClient.send({ type: 'set_settings', ...patch })
  }, [])

  const resetSettings = useCallback(() => {
    engineClient.send({ type: 'set_settings', reset: true })
  }, [])

  // Requests, like everything else here. The measurement runs in the engine — only it sees every
  // frame's anchor and pinch distance — and its progress arrives as `calibration` messages.
  const startMeasurement = useCallback((step: CalibrationStep) => {
    engineClient.send({ type: 'calibrate', action: 'start', step })
  }, [])

  const cancelMeasurement = useCallback(() => {
    engineClient.send({ type: 'calibrate', action: 'cancel' })
  }, [])

  const completeCalibration = useCallback(() => {
    engineClient.send({ type: 'calibrate', action: 'complete' })
  }, [])

  const discoverCameras = useCallback(() => {
    engineClient.send({ type: 'command', action: 'discover_cameras' })
  }, [])

  const selectCamera = useCallback((index: number) => {
    engineClient.send({ type: 'select_camera', index })
  }, [])

  // Not a `send` like the rest: there is no socket to send it on. This is the one command that
  // exists precisely because the connection is not there.
  const reconnect = useCallback(() => {
    engineClient.connect()
  }, [])

  return {
    start,
    stop,
    pause,
    enableCursor,
    disableCursor,
    updateSettings,
    resetSettings,
    startMeasurement,
    cancelMeasurement,
    completeCalibration,
    discoverCameras,
    selectCamera,
    reconnect,
  }
}

/** True when the engine is connected and authenticated. */
export function useIsConnected(): boolean {
  return useConnectionStore((state) => state.phase === 'connected')
}

/** True when the engine reports it is actively tracking. */
export function useIsTracking(): boolean {
  return useTrackingStore((state) => state.tracking === 'running')
}
