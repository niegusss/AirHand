import { describe, expect, it } from 'vitest'

import { isProtocolCompatible, parseServerMessage, PROTOCOL_VERSION } from './protocol'

/**
 * Wire parsing.
 *
 * The parser's job is not to describe the protocol a second time — it is to make sure nothing
 * unrecognized reaches the rest of the app. So the tests here are mostly about what gets
 * *rejected*: a malformed message that slips through becomes a render crash or, worse, a control
 * that quietly shows the wrong state.
 */

const SETTINGS = {
  type: 'settings',
  gesture: { pinchClose: 0.35, pinchOpen: 0.55 },
  pointer: { minCutoff: 0.8, beta: 6, holdOnPinch: true },
  cursor: { coverage: 0.7 },
  bounds: { cursor: { coverage: [0.2, 1] } },
  defaults: { cursor: { coverage: 0.7 } },
  profile: { path: 'C:\\profile.json', loaded: true, stale: false, reason: null },
}

describe('parseServerMessage', () => {
  it('accepts a settings message', () => {
    const message = parseServerMessage(JSON.stringify(SETTINGS))

    expect(message?.type).toBe('settings')
    expect(message).toMatchObject({ cursor: { coverage: 0.7 } })
  })

  it('carries bounds and defaults through', () => {
    // Both ship on every settings message so the UI never keeps its own copy of the ranges.
    const message = parseServerMessage(JSON.stringify(SETTINGS))

    expect(message).toMatchObject({
      bounds: { cursor: { coverage: [0.2, 1] } },
      defaults: { cursor: { coverage: 0.7 } },
    })
  })

  it('rejects a settings message that is missing a section', () => {
    const { pointer: _pointer, ...incomplete } = SETTINGS
    expect(parseServerMessage(JSON.stringify(incomplete))).toBeNull()
  })

  it('rejects a section that is an array rather than an object', () => {
    expect(parseServerMessage(JSON.stringify({ ...SETTINGS, cursor: [0.7] }))).toBeNull()
  })

  it('rejects a section that is null', () => {
    // `typeof null === 'object'` is the classic way this check gets written wrong.
    expect(parseServerMessage(JSON.stringify({ ...SETTINGS, gesture: null }))).toBeNull()
  })

  it('accepts a calibration message', () => {
    const message = parseServerMessage(
      JSON.stringify({
        type: 'calibration',
        step: 'pinch',
        state: 'done',
        samples: 231,
        secondsRemaining: 0,
        secondsTotal: 8,
        measurement: { restingLevel: 0.94, attempts: 3 },
        suggestion: { gesture: { pinchClose: 0.46, pinchOpen: 0.66 } },
        reason: null,
      }),
    )

    expect(message?.type).toBe('calibration')
    expect(message).toMatchObject({ suggestion: { gesture: { pinchClose: 0.46 } } })
  })

  it('rejects a calibration message with no step to render', () => {
    // `step` and `state` are what the wizard branches on; without them there is nothing to show.
    expect(
      parseServerMessage(JSON.stringify({ type: 'calibration', state: 'done' })),
    ).toBeNull()
  })

  it('rejects an unknown message type', () => {
    expect(parseServerMessage(JSON.stringify({ type: 'set_settings' }))).toBeNull()
  })

  it('rejects malformed JSON and non-strings', () => {
    expect(parseServerMessage('{ not json')).toBeNull()
    expect(parseServerMessage(null)).toBeNull()
    expect(parseServerMessage(new ArrayBuffer(4))).toBeNull()
  })

  it('accepts an invalid_settings error', () => {
    const message = parseServerMessage(
      JSON.stringify({ type: 'error', code: 'invalid_settings', message: 'too small' }),
    )
    expect(message).toMatchObject({ type: 'error', code: 'invalid_settings' })
  })
})

describe('isProtocolCompatible', () => {
  it('accepts a newer minor version', () => {
    // Minor bumps are additive by contract, so an older client keeps working.
    expect(isProtocolCompatible('1.99.0')).toBe(true)
  })

  it('refuses a different major version', () => {
    expect(isProtocolCompatible('2.0.0')).toBe(false)
  })

  it('reads its own version from the shared protocol file', () => {
    // Injected at build time from shared/protocol/protocol.json — the same file Python reads.
    // Hand-mirroring it in TypeScript is the drift this mechanism exists to prevent.
    expect(PROTOCOL_VERSION).toMatch(/^\d+\.\d+\.\d+$/)
    expect(isProtocolCompatible(PROTOCOL_VERSION)).toBe(true)
  })
})
