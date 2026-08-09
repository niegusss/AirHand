import { create } from 'zustand'

import type {
  ActiveArea,
  EngineSettings,
  ProfileInfo,
  SettingsBounds,
  SettingsMessage,
} from '@/lib/protocol'

/**
 * Engine settings, as the engine last reported them.
 *
 * **The engine is authoritative and this store holds no optimistic state.** A patch is a proposal:
 * the engine validates it, and a refused one leaves the previous values in force with an
 * `invalid_settings` error instead of a new `settings` message. Rendering a locally-applied value
 * would show a slider in a position the engine had already rejected — the same trap `cursorEnabled`
 * exists to avoid.
 *
 * `bounds` and `defaults` arrive with every message, so nothing here hardcodes a range or a
 * "restore defaults" value. A second copy of those numbers is one that drifts from the engine's.
 */
interface SettingsState {
  /** Null until the first `settings` message arrives — no engine, nothing to show. */
  settings: EngineSettings | null
  bounds: SettingsBounds | null
  defaults: EngineSettings | null
  /** Null when the engine runs with `--no-profile`: nothing is being persisted. */
  profile: ProfileInfo | null
  /**
   * The rectangle the cursor settings actually produce, computed by the engine.
   *
   * Null until it knows both the screen and the camera. Lives here rather than in the tracking
   * store because it is derived from these settings and arrives in the same message.
   */
  activeArea: ActiveArea | null
  /** Last refusal, cleared by the next accepted change. */
  lastRejection: string | null

  applySettings: (message: SettingsMessage) => void
  setRejection: (message: string) => void
  reset: () => void
}

const INITIAL = {
  settings: null,
  bounds: null,
  defaults: null,
  profile: null,
  activeArea: null,
  lastRejection: null,
}

export const useSettingsStore = create<SettingsState>((set) => ({
  ...INITIAL,

  applySettings: ({ gesture, pointer, cursor, bounds, defaults, profile, activeArea }) =>
    // Replaced wholesale, never merged into what is already here. The engine sends complete
    // settings every time, so merging could only ever preserve a value the engine no longer holds.
    set({
      settings: { gesture, pointer, cursor },
      bounds,
      defaults,
      profile,
      activeArea: activeArea ?? null,
      lastRejection: null,
    }),

  setRejection: (lastRejection) => set({ lastRejection }),
  reset: () => set({ ...INITIAL }),
}))
