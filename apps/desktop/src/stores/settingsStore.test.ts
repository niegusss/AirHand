import { beforeEach, describe, expect, it } from 'vitest'

import type { SettingsMessage } from '@/lib/protocol'
import { useSettingsStore } from './settingsStore'

/**
 * Settings store.
 *
 * The behaviour worth pinning down is that this store **never invents state**. The engine is
 * authoritative: it validates every change and can refuse one, so a value shown here that the
 * engine never confirmed would be a slider sitting in a position the engine had already rejected.
 */

function message(overrides: Partial<SettingsMessage> = {}): SettingsMessage {
  return {
    type: 'settings',
    gesture: {
      pinchClose: 0.35,
      pinchOpen: 0.55,
      holdToDragSeconds: 0.4,
      clickLatchSeconds: 0.2,
      extendedAngleDegrees: 150,
      scrollStep: 0.25,
      dropoutGraceSeconds: 0.15,
    },
    pointer: {
      minCutoff: 0.8,
      beta: 10,
      dCutoff: 1,
      holdOnPinch: true,
      dropoutGraceSeconds: 0.2,
    },
    cursor: { coverage: 0.7, centerX: 0.5, centerY: 0.5 },
    bounds: {
      gesture: {
        pinchClose: [0.05, 0.9],
        pinchOpen: [0.1, 1.5],
        holdToDragSeconds: [0.1, 2],
        clickLatchSeconds: [0.05, 1],
        extendedAngleDegrees: [90, 180],
        scrollStep: [0.05, 1.5],
        dropoutGraceSeconds: [0, 0.5],
      },
      pointer: {
        minCutoff: [0.1, 5],
        beta: [0, 30],
        dCutoff: [0.1, 5],
        holdOnPinch: null,
        dropoutGraceSeconds: [0, 1],
      },
      cursor: { coverage: [0.2, 1], centerX: [0.2, 0.8], centerY: [0.2, 0.8] },
    },
    defaults: {
      gesture: {
        pinchClose: 0.35,
        pinchOpen: 0.55,
        holdToDragSeconds: 0.4,
        clickLatchSeconds: 0.2,
        extendedAngleDegrees: 150,
        scrollStep: 0.25,
        dropoutGraceSeconds: 0.15,
      },
      pointer: {
        minCutoff: 1.5,
        beta: 10,
        dCutoff: 1,
        holdOnPinch: true,
        dropoutGraceSeconds: 0.2,
      },
      cursor: { coverage: 0.7, centerX: 0.5, centerY: 0.5 },
    },
    profile: {
      path: 'C:\\profile.json',
      loaded: true,
      stale: false,
      reason: null,
      calibrated: true,
      savedAt: '2026-08-09T13:41:39+02:00',
    },
    activeArea: { left: 0.15, top: 0.24, width: 0.7, height: 0.52 },
    ...overrides,
  }
}

beforeEach(() => {
  useSettingsStore.getState().reset()
})

describe('applySettings', () => {
  it('starts empty, because an unconnected app knows nothing', () => {
    expect(useSettingsStore.getState().settings).toBeNull()
    expect(useSettingsStore.getState().bounds).toBeNull()
  })

  it('stores the values, bounds, defaults and profile', () => {
    useSettingsStore.getState().applySettings(message())
    const state = useSettingsStore.getState()

    expect(state.settings?.cursor.coverage).toBe(0.7)
    expect(state.bounds?.cursor.coverage).toEqual([0.2, 1])
    expect(state.defaults?.pointer.beta).toBe(10)
    expect(state.profile?.loaded).toBe(true)
  })

  it('replaces the previous settings rather than merging into them', () => {
    // The engine sends complete settings every time, so a merge could only ever preserve a value
    // the engine no longer holds — and it would do it silently.
    useSettingsStore.getState().applySettings(message())
    useSettingsStore.getState().applySettings(
      message({
        cursor: { coverage: 0.9, centerX: 0.5, centerY: 0.5 },
        pointer: { ...message().pointer, beta: 12 },
      }),
    )

    const settings = useSettingsStore.getState().settings
    expect(settings?.cursor.coverage).toBe(0.9)
    expect(settings?.pointer.beta).toBe(12)
  })

  it('does not keep the message envelope in the stored settings', () => {
    useSettingsStore.getState().applySettings(message())
    expect(Object.keys(useSettingsStore.getState().settings ?? {}).sort()).toEqual([
      'cursor',
      'gesture',
      'pointer',
    ])
  })

  it('reports a null profile when the engine persists nothing', () => {
    // `--no-profile`. The UI has to be able to say "changes will not be saved".
    useSettingsStore.getState().applySettings(message({ profile: null }))
    expect(useSettingsStore.getState().profile).toBeNull()
  })

  it('keeps the active area the engine computed', () => {
    // Drawn, never derived: reproducing `active_area_for` here would be CV logic in the frontend.
    useSettingsStore.getState().applySettings(message())
    expect(useSettingsStore.getState().activeArea).toEqual({
      left: 0.15,
      top: 0.24,
      width: 0.7,
      height: 0.52,
    })
  })

  it('reports no active area when the engine cannot compute one', () => {
    // No screen, or no camera yet. An honest null beats a guessed rectangle.
    useSettingsStore.getState().applySettings(message({ activeArea: null }))
    expect(useSettingsStore.getState().activeArea).toBeNull()
  })
})

describe('rejection', () => {
  it('records a refusal without touching the settings', () => {
    useSettingsStore.getState().applySettings(message())
    useSettingsStore.getState().setRejection('cursor.coverage must be between 0.2 and 1.0')

    const state = useSettingsStore.getState()
    expect(state.lastRejection).toContain('between')
    // The engine kept the old value, so the UI must show the old value.
    expect(state.settings?.cursor.coverage).toBe(0.7)
  })

  it('clears the refusal once a change is accepted', () => {
    useSettingsStore.getState().setRejection('too small')
    useSettingsStore
      .getState()
      .applySettings(message({ cursor: { coverage: 0.5, centerX: 0.5, centerY: 0.5 } }))

    expect(useSettingsStore.getState().lastRejection).toBeNull()
  })
})

describe('reset', () => {
  it('clears everything, for a disconnect', () => {
    // Settings describe an engine that is no longer there; a disconnected app must not offer
    // editable controls that go nowhere.
    useSettingsStore.getState().applySettings(message())
    useSettingsStore.getState().reset()

    const state = useSettingsStore.getState()
    expect(state.settings).toBeNull()
    expect(state.defaults).toBeNull()
    expect(state.profile).toBeNull()
  })
})
