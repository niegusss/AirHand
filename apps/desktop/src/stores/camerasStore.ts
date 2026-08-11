import { create } from 'zustand'

import type { CameraDevice, CamerasMessage } from '@/lib/protocol'

/**
 * The capture devices the engine knows about, and which one it will open.
 *
 * **Same rule as `settingsStore` and `calibrationStore`: no optimistic state.** Clicking a device
 * sends a request; what is rendered is what came back. A locally-selected radio would stay selected
 * through a refusal, and — worse here than anywhere else — through a device that failed to open,
 * showing a camera as chosen while the status said `error` about a different one.
 *
 * `devices` being empty is not an error state. Probing opens and releases hardware, so the engine
 * never does it unasked; until someone scans, the honest answer is that nothing has looked.
 */
interface CamerasState {
  devices: CameraDevice[]
  /** A probe is in flight. The pipeline is down for its duration and comes back on its own. */
  scanning: boolean
  /**
   * What the engine opens next. Null means the user has never chosen — distinct from 0, which is
   * both a real device and the engine's default, and the two must not be conflated.
   */
  selected: number | null
  /** Why the last scan or selection could not be honoured. */
  reason: string | null
  /** Whether a scan has completed on this connection, so "none found" can be said honestly. */
  scanned: boolean

  applyCameras: (message: CamerasMessage) => void
  reset: () => void
}

const INITIAL = {
  devices: [],
  scanning: false,
  selected: null,
  reason: null,
  scanned: false,
}

export const useCamerasStore = create<CamerasState>((set) => ({
  ...INITIAL,

  applyCameras: ({ devices, scanning, selected, reason }) =>
    set((state) => ({
      devices,
      scanning,
      selected,
      reason,
      // Set by the message that ends a scan, never cleared by a later selection. It is what
      // separates "nobody has looked yet" from "we looked and found nothing" — two states that
      // both render an empty list and need opposite text under it.
      scanned: state.scanned || (state.scanning && !scanning),
    })),

  reset: () => set({ ...INITIAL }),
}))
