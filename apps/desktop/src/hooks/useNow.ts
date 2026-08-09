import { useEffect, useState } from 'react'

/**
 * A clock that ticks on an interval.
 *
 * Needed for staleness checks: when the telemetry stream stalls, nothing in the store changes,
 * so nothing re-renders — and a stall that never surfaces looks identical to healthy tracking.
 * This gives the UI its own reason to re-evaluate.
 *
 * Keep the interval coarse. This is for detecting "no data for a second", not for display.
 */
export function useNow(intervalMs = 500): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])

  return now
}
