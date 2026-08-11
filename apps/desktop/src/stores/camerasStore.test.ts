import { beforeEach, describe, expect, it } from 'vitest'

import type { CamerasMessage } from '@/lib/protocol'
import { useCamerasStore } from './camerasStore'

/**
 * The device list, as the engine reported it.
 *
 * The only real logic in this store is `scanned`, and it exists because two very different
 * situations both render an empty list: nobody has scanned yet, and a scan found nothing. They need
 * opposite text under them, and the difference is not in the message — it is in whether a scan
 * happened at all.
 */
function message(overrides: Partial<CamerasMessage> = {}): CamerasMessage {
  return {
    type: 'cameras',
    scanning: false,
    devices: [],
    selected: null,
    reason: null,
    ...overrides,
  }
}

describe('camerasStore', () => {
  beforeEach(() => {
    useCamerasStore.getState().reset()
  })

  it('renders what arrived rather than what was requested', () => {
    useCamerasStore.getState().applyCameras(
      message({ devices: [{ index: 2, name: 'Camera 2 (MSMF)', width: 640, height: 480 }], selected: 2 }),
    )

    expect(useCamerasStore.getState().selected).toBe(2)
    expect(useCamerasStore.getState().devices).toHaveLength(1)
  })

  it('keeps null and zero apart', () => {
    // The engine's default device is also index 0, so "never chosen" and "chose the first one"
    // have to stay distinguishable or the picker cannot say which it is.
    expect(useCamerasStore.getState().selected).toBeNull()

    useCamerasStore.getState().applyCameras(message({ selected: 0 }))
    expect(useCamerasStore.getState().selected).toBe(0)
  })

  it('does not claim a scan happened before one has', () => {
    useCamerasStore.getState().applyCameras(message())
    expect(useCamerasStore.getState().scanned).toBe(false)
  })

  it('records that a scan finished, so an empty list can be explained', () => {
    const store = useCamerasStore.getState()
    store.applyCameras(message({ scanning: true }))
    expect(useCamerasStore.getState().scanned).toBe(false)

    store.applyCameras(message({ scanning: false }))
    expect(useCamerasStore.getState().scanned).toBe(true)
  })

  it('does not forget a completed scan when a later selection arrives', () => {
    const store = useCamerasStore.getState()
    store.applyCameras(message({ scanning: true }))
    store.applyCameras(message({ scanning: false }))
    store.applyCameras(message({ selected: 1 }))

    expect(useCamerasStore.getState().scanned).toBe(true)
  })

  it('drops everything on reset', () => {
    // A device list describes the engine that enumerated it, and `scanning` left true by a
    // connection that dropped mid-probe would disable the scan button forever.
    const store = useCamerasStore.getState()
    store.applyCameras(message({ scanning: true, selected: 1 }))
    store.reset()

    const state = useCamerasStore.getState()
    expect(state.scanning).toBe(false)
    expect(state.selected).toBeNull()
    expect(state.devices).toEqual([])
  })
})
