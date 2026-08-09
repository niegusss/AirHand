import { useEffect } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { shouldCalibrate } from '@/lib/calibration'
import { useSettingsStore } from '@/stores/settingsStore'

/** Routes that stay reachable while calibration is outstanding. */
const ALWAYS_ALLOWED = ['/calibration', '/diagnostics']

interface CalibrationGateProps {
  children: React.ReactNode
}

/**
 * Sends a user who has never calibrated to the wizard, per `projectbrief.md`: **mandatory on first
 * run, cannot be skipped.**
 *
 * ## Diagnostics stays reachable, deliberately
 *
 * The wizard cannot be completed without a working camera, so a gate with no exits turns a broken
 * webcam into an app that shows one screen forever and will not say why. Diagnostics is where the
 * why lives, so it stays open. The gate still does its job: the Dashboard and Settings — the
 * screens that imply a configured, working system — are closed until the wizard is finished.
 *
 * ## It waits for the engine
 *
 * `profile` is null both when the engine persists nothing *and* before the first `settings`
 * message arrives. Rendering the children until the engine has spoken avoids a redirect flash on
 * every launch for a user who calibrated months ago.
 */
export function CalibrationGate({ children }: CalibrationGateProps) {
  const location = useLocation()
  const profile = useSettingsStore((state) => state.profile)
  const required = shouldCalibrate(profile)

  useEffect(() => {
    if (required && !ALWAYS_ALLOWED.includes(location.pathname)) {
      document.title = 'AirHand Mouse — calibration required'
    } else {
      document.title = 'AirHand Mouse'
    }
  }, [required, location.pathname])

  if (required && !ALWAYS_ALLOWED.includes(location.pathname)) {
    return <Navigate to="/calibration" replace />
  }

  return <>{children}</>
}
