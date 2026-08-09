import { create } from 'zustand'

import type { CalibrationMessage } from '@/lib/protocol'

/**
 * The measurement the engine is running, exactly as it reported it.
 *
 * **Same rule as `settingsStore`: no optimistic state.** Pressing "Measure" sends a request; what
 * the UI renders is what came back. A locally-started countdown would keep ticking through a
 * refusal, a disconnect, or a session the engine cancelled when tracking stopped.
 *
 * Which step the wizard is *showing* is not here. That is navigation — local component state — and
 * putting it in the store would make going back to re-read an earlier step look like restarting
 * its measurement.
 */
interface CalibrationState {
  /** Null until a measurement has been requested on this connection. */
  session: CalibrationMessage | null

  applyCalibration: (message: CalibrationMessage) => void
  reset: () => void
}

export const useCalibrationStore = create<CalibrationState>((set) => ({
  session: null,

  // Replaced wholesale. The engine sends a complete picture each time, so merging could only
  // preserve a field from a measurement that is over.
  applyCalibration: (session) => set({ session }),

  reset: () => set({ session: null }),
}))
