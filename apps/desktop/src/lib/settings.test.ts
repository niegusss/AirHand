import { describe, expect, it } from 'vitest'

import {
  DRIVEN_AS_PAIR,
  SETTINGS_GROUPS,
  bandBounds,
  pinchPatch,
  type KnobSpec,
} from '@/lib/settings'
import type { EngineSettings } from '@/lib/protocol'

/**
 * A complete settings value, written out field by field on purpose.
 *
 * TypeScript will not let this object miss a knob, so it is the runtime list of everything the
 * engine can be asked to change — without a second hand-maintained copy of the names.
 */
const EVERY_KNOB: EngineSettings = {
  gesture: {
    pinchClose: 0.5,
    pinchOpen: 0.7,
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
}

function everyDeclaredKnob(): KnobSpec[] {
  return SETTINGS_GROUPS.flatMap((group) => group.knobs)
}

describe('SETTINGS_GROUPS', () => {
  it('reaches every knob the engine accepts', () => {
    // The Python side already refuses to let a settable field exist without a declared knob
    // (`test_every_settable_field_has_a_declared_knob`). This is the other half: a knob that
    // reaches the wire and then has no control is invisible rather than broken, so nothing else
    // would ever report it.
    const declared = new Set(everyDeclaredKnob().map((knob) => `${knob.section}.${knob.knob}`))

    for (const [section, values] of Object.entries(EVERY_KNOB)) {
      for (const knob of Object.keys(values)) {
        const name = `${section}.${knob}`
        expect(
          declared.has(name) || DRIVEN_AS_PAIR.has(name),
          `${name} is on the wire but no control changes it`,
        ).toBe(true)
      }
    }
  })

  it('declares no knob twice', () => {
    // Two sliders for one value disagree the moment one of them is moved.
    const names = everyDeclaredKnob().map((knob) => `${knob.section}.${knob.knob}`)
    expect(new Set(names).size).toBe(names.length)
  })

  it('declares nothing the engine does not accept', () => {
    for (const knob of everyDeclaredKnob()) {
      const section = EVERY_KNOB[knob.section] as Record<string, unknown>
      expect(section, `unknown section ${knob.section}`).toBeDefined()
      expect(knob.knob in section, `${knob.section}.${knob.knob} is not a real knob`).toBe(true)
    }
  })

  it('keeps the paired knob out of the plain groups', () => {
    // `pinchOpen` is written by the pinch pair control, which sends it together with `pinchClose`.
    // A second, independent slider for it is exactly how the hysteresis invariant gets broken.
    const names = everyDeclaredKnob().map((knob) => `${knob.section}.${knob.knob}`)
    for (const paired of DRIVEN_AS_PAIR) expect(names).not.toContain(paired)
  })
})

describe('pinchPatch', () => {
  it('always writes both halves of the pair', () => {
    // The engine validates `pinch_open > pinch_close` inside the dataclass, after the per-field
    // range checks. Sending one without the other is what makes that invariant reachable.
    expect(pinchPatch(0.5, 0.2)).toEqual({ gesture: { pinchClose: 0.5, pinchOpen: 0.7 } })
  })

  it('keeps the threshold below the release point for any positive band', () => {
    for (const close of [0.05, 0.24, 0.5, 0.9]) {
      for (const band of [0.01, 0.2, 0.6]) {
        const { gesture } = pinchPatch(close, band)
        expect(gesture.pinchOpen).toBeGreaterThan(gesture.pinchClose)
      }
    }
  })

  it('preserves the band when only the threshold moves', () => {
    // Same behaviour as the engine's own pinch derivation, which carries the existing band across
    // rather than inventing a new one.
    const before = pinchPatch(0.24, 0.2).gesture
    const after = pinchPatch(0.6, 0.2).gesture
    expect(after.pinchOpen - after.pinchClose).toBeCloseTo(before.pinchOpen - before.pinchClose)
  })

  it('rounds away the float dust a slider produces', () => {
    // 0.44 + 0.15 is 0.5900000000000001 in binary floating point, and that lands in the profile
    // on disk and in the readout next to the slider.
    expect(pinchPatch(0.44, 0.15).gesture.pinchOpen).toBe(0.59)
  })
})

describe('bandBounds', () => {
  const open: [number, number] = [0.1, 1.5]

  it('derives the range from the wire rather than declaring one', () => {
    expect(bandBounds(0.5, open, 0.01)).toEqual([0.01, 1.0])
  })

  it('never offers a band the engine would refuse', () => {
    // At the top of `pinchClose`'s range there is only 0.6 of headroom left under `pinchOpen`.
    const [, high] = bandBounds(0.9, open, 0.01)
    expect(0.9 + high).toBeLessThanOrEqual(open[1])
  })

  it('lifts the floor when the threshold sits below pinchOpen’s own minimum', () => {
    // `pinchClose` may go down to 0.05 but `pinchOpen` may not go below 0.1, so at the very bottom
    // the smallest legal band is 0.05 rather than one step.
    expect(bandBounds(0.05, open, 0.01)[0]).toBeCloseTo(0.05)
  })

  it('keeps a usable band even where the ranges leave none', () => {
    // Hysteresis with no gap is the phantom-click failure the engine refuses outright, so the
    // floor is the step — the smallest gap a slider can express — not zero.
    const [low, high] = bandBounds(1.5, open, 0.01)
    expect(low).toBe(0.01)
    expect(high).toBeGreaterThanOrEqual(low)
  })
})
