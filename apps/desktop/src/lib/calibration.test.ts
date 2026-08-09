import { describe, expect, it } from 'vitest'

import { describeSuggestion, shouldCalibrate } from '@/lib/calibration'
import type { ProfileInfo } from '@/lib/protocol'

function profile(overrides: Partial<ProfileInfo> = {}): ProfileInfo {
  return {
    path: 'C:\\Users\\x\\AppData\\Local\\AirHand\\profile.json',
    loaded: true,
    stale: false,
    reason: null,
    calibrated: true,
    ...overrides,
  }
}

describe('shouldCalibrate', () => {
  it('does not gate an engine that persists nothing', () => {
    // `--no-profile`. A wizard whose result evaporates on exit is worse than no wizard.
    expect(shouldCalibrate(null)).toBe(false)
  })

  it('lets a calibrated user straight through', () => {
    expect(shouldCalibrate(profile())).toBe(false)
  })

  it('gates a profile that exists but was never calibrated', () => {
    // The trap this field exists for: every settings change writes the profile, so `loaded` is
    // true the moment anyone nudges a slider.
    expect(shouldCalibrate(profile({ loaded: true, calibrated: false }))).toBe(true)
  })

  it('gates a stale profile even though it was calibrated', () => {
    // Calibrated against a different MediaPipe model — its numbers are not evidence about this one.
    expect(shouldCalibrate(profile({ stale: true, calibrated: true }))).toBe(true)
  })
})

describe('describeSuggestion', () => {
  it('names every numeric knob in the patch', () => {
    const lines = describeSuggestion({ gesture: { pinchClose: 0.46, pinchOpen: 0.66 } })
    expect(lines).toEqual(['Pinch closes at: 0.46', 'Pinch opens at: 0.66'])
  })

  it('ignores the reset flag rather than rendering it as a value', () => {
    expect(describeSuggestion({ reset: true })).toEqual([])
  })
})
