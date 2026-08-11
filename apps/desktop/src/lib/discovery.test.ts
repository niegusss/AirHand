import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { invoke } from '@tauri-apps/api/core'

import { discoverEngine } from './discovery'
import { PROTOCOL_VERSION } from './protocol'

vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn() }))

const invoked = vi.mocked(invoke)

/** The shape `read_handshake` returns. See `src-tauri/src/handshake.rs`. */
function handshake(overrides: Record<string, unknown> = {}) {
  return {
    pid: 4242,
    port: 51873,
    protocolVersion: PROTOCOL_VERSION,
    token: 'per-launch-token',
    startedAt: '2026-08-11T10:15:00Z',
    ...overrides,
  }
}

function insideTauri(): void {
  vi.stubGlobal('window', { __TAURI_INTERNALS__: {} })
}

function withDevOverride(): void {
  vi.stubEnv('VITE_AIRHAND_WS_URL', 'ws://127.0.0.1:8765')
  vi.stubEnv('VITE_AIRHAND_WS_TOKEN', 'dev-token')
}

beforeEach(() => {
  invoked.mockReset()
  // Vite loads the real `.env.local` into `import.meta.env`, so on any development machine every
  // test would silently be running the dev-override path. Neutralize it and opt back in.
  vi.stubEnv('VITE_AIRHAND_WS_URL', undefined)
  vi.stubEnv('VITE_AIRHAND_WS_TOKEN', undefined)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('in a browser', () => {
  it('uses the development override', async () => {
    withDevOverride()

    await expect(discoverEngine()).resolves.toEqual({
      ok: true,
      url: 'ws://127.0.0.1:8765',
      token: 'dev-token',
    })
    expect(invoked).not.toHaveBeenCalled()
  })

  it('says so plainly when there is no override, rather than blaming the engine', async () => {
    const result = await discoverEngine()

    expect(result).toMatchObject({ ok: false, reason: 'no-dev-override' })
  })
})

describe('inside the desktop shell', () => {
  it('builds a loopback endpoint from the published handshake', async () => {
    insideTauri()
    invoked.mockResolvedValue(handshake())

    await expect(discoverEngine()).resolves.toEqual({
      ok: true,
      url: 'ws://127.0.0.1:51873',
      token: 'per-launch-token',
    })
    expect(invoked).toHaveBeenCalledWith('read_handshake')
  })

  it('prefers a working handshake over a development override', async () => {
    // Otherwise the packaged path would never run on a machine that has a .env.local — which is
    // every machine this is developed on.
    insideTauri()
    withDevOverride()
    invoked.mockResolvedValue(handshake())

    await expect(discoverEngine()).resolves.toMatchObject({ url: 'ws://127.0.0.1:51873' })
  })

  it('reports the reader\'s own reason for a stale handshake', async () => {
    insideTauri()
    invoked.mockRejectedValue({
      reason: 'handshake-stale',
      message: 'process 4242 is no longer running',
    })

    const result = await discoverEngine()

    expect(result).toMatchObject({ ok: false, reason: 'handshake-stale' })
    expect(result).toHaveProperty('message', 'process 4242 is no longer running')
  })

  it('distinguishes a missing handshake from an unreadable one', async () => {
    insideTauri()
    invoked.mockRejectedValue({ reason: 'handshake-missing', message: 'no engine has run' })

    await expect(discoverEngine()).resolves.toMatchObject({ reason: 'handshake-missing' })
  })

  it('falls back to the development override when the handshake fails', async () => {
    insideTauri()
    withDevOverride()
    invoked.mockRejectedValue({ reason: 'handshake-missing', message: 'no engine has run' })

    await expect(discoverEngine()).resolves.toMatchObject({ url: 'ws://127.0.0.1:8765' })
  })

  it('refuses an endpoint whose protocol major differs, before opening a socket', async () => {
    insideTauri()
    invoked.mockResolvedValue(handshake({ protocolVersion: '99.0.0' }))

    await expect(discoverEngine()).resolves.toMatchObject({
      ok: false,
      reason: 'handshake-version-mismatch',
    })
  })

  it('accepts a minor-version difference, which the protocol declares compatible', async () => {
    insideTauri()
    const [major] = PROTOCOL_VERSION.split('.')
    invoked.mockResolvedValue(handshake({ protocolVersion: `${major}.0.0` }))

    await expect(discoverEngine()).resolves.toMatchObject({ ok: true })
  })

  it('treats a broken invoke as unreadable rather than passing its string through as a reason', async () => {
    insideTauri()
    invoked.mockRejectedValue('command read_handshake not found')

    const result = await discoverEngine()

    expect(result).toMatchObject({ ok: false, reason: 'handshake-unreadable' })
  })
})
