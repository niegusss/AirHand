/**
 * Engine discovery.
 *
 * Two strategies behind one interface:
 *
 *  - **handshake** — read `%LOCALAPPDATA%\AirHand\runtime.json` through the Tauri Rust layer,
 *    which validates that the publishing process is still alive. This is the production path; a
 *    browser cannot read the filesystem, which is why it lives in Rust.
 *  - **dev-override** — `VITE_AIRHAND_WS_URL` + `VITE_AIRHAND_WS_TOKEN`. Used in browser
 *    development, where the engine is started manually with pinned `--port` / `--token`.
 *
 * **Inside Tauri the handshake goes first**, with the override as a fallback. The other order
 * would mean that on any machine carrying a `.env.local` — every development machine — the
 * packaged path is never the one actually exercised.
 */

import { invoke } from '@tauri-apps/api/core'

import { isProtocolCompatible, PROTOCOL_VERSION } from './protocol'

export interface Endpoint {
  url: string
  token: string
}

export type DiscoveryFailureReason =
  | 'no-dev-override'
  | 'handshake-missing'
  | 'handshake-unreadable'
  | 'handshake-stale'
  | 'handshake-version-mismatch'

export interface DiscoveryFailure {
  ok: false
  reason: DiscoveryFailureReason
  message: string
}

export type DiscoveryResult = ({ ok: true } & Endpoint) | DiscoveryFailure

/** What `read_handshake` returns — the published fields, unjudged. See `src-tauri/src/handshake.rs`. */
interface Handshake {
  pid: number
  port: number
  protocolVersion: string
  token: string
  startedAt?: string | null
}

/** The reasons the Rust reader can name. Anything else is a broken invoke, not a bad handshake. */
const READER_REASONS = new Set<DiscoveryFailureReason>([
  'handshake-missing',
  'handshake-unreadable',
  'handshake-stale',
])

/** True when running inside the Tauri shell rather than a plain browser. */
export function isTauriRuntime(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export async function discoverEngine(): Promise<DiscoveryResult> {
  if (isTauriRuntime()) {
    const handshake = await fromHandshake()
    if (handshake.ok) return handshake

    // The override is a deliberate escape hatch, so it beats a *failed* handshake — but never a
    // working one.
    return devOverride() ?? handshake
  }

  return (
    devOverride() ?? {
      ok: false,
      reason: 'no-dev-override',
      message:
        'No engine endpoint. A browser cannot read the handshake file, so development needs ' +
        'VITE_AIRHAND_WS_URL and VITE_AIRHAND_WS_TOKEN in .env.local, with the engine started ' +
        'using matching --port and --token flags.',
    }
  )
}

function devOverride(): ({ ok: true } & Endpoint) | null {
  const url = import.meta.env.VITE_AIRHAND_WS_URL
  const token = import.meta.env.VITE_AIRHAND_WS_TOKEN

  return url && token ? { ok: true, url, token } : null
}

async function fromHandshake(): Promise<DiscoveryResult> {
  let handshake: Handshake
  try {
    handshake = await invoke<Handshake>('read_handshake')
  } catch (cause) {
    return asFailure(cause)
  }

  // Checked here rather than in Rust, and *before* the socket opens. The server says the same
  // thing in `hello`, but by then the connection exists and the error arrives as a disconnect
  // instead of as a reason not to connect.
  if (!isProtocolCompatible(handshake.protocolVersion)) {
    return {
      ok: false,
      reason: 'handshake-version-mismatch',
      message:
        `The running engine speaks protocol ${handshake.protocolVersion}, this app speaks ` +
        `${PROTOCOL_VERSION}. Rebuild both from the same shared/protocol.`,
    }
  }

  // The engine binds loopback only, so the host is not the handshake's to choose.
  return { ok: true, url: `ws://127.0.0.1:${handshake.port}`, token: handshake.token }
}

function asFailure(cause: unknown): DiscoveryFailure {
  if (
    typeof cause === 'object' &&
    cause !== null &&
    'reason' in cause &&
    READER_REASONS.has((cause as DiscoveryFailure).reason)
  ) {
    const failure = cause as DiscoveryFailure
    return { ok: false, reason: failure.reason, message: failure.message }
  }

  // Not a reader failure: the command is missing, the IPC is blocked, something structural.
  return {
    ok: false,
    reason: 'handshake-unreadable',
    message: `The desktop shell could not read the engine handshake: ${String(cause)}`,
  }
}
