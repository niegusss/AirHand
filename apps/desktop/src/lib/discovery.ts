/**
 * Engine discovery.
 *
 * Two strategies behind one interface:
 *
 *  - **dev-override** — `VITE_AIRHAND_WS_URL` + `VITE_AIRHAND_WS_TOKEN`. Used in browser
 *    development, where the engine is started manually with pinned `--port` / `--token`.
 *  - **handshake** — read `%LOCALAPPDATA%\AirHand\runtime.json`, validate the pid is alive, and
 *    return its port + token. This is the production path and belongs to Tauri's Rust layer,
 *    because a browser cannot read the filesystem.
 *
 * The handshake strategy is not implemented yet (Tauri is deferred to the next pass). It reports
 * a named, honest failure rather than pretending to work — see systemPatterns.md, which requires
 * that no screen appear functional while disconnected.
 */

export interface Endpoint {
  url: string
  token: string
}

export type DiscoveryFailureReason = 'tauri-not-available' | 'no-dev-override' | 'handshake-missing'

export interface DiscoveryFailure {
  ok: false
  reason: DiscoveryFailureReason
  message: string
}

export type DiscoveryResult = ({ ok: true } & Endpoint) | DiscoveryFailure

/** True when running inside the Tauri shell rather than a plain browser. */
export function isTauriRuntime(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}

export async function discoverEngine(): Promise<DiscoveryResult> {
  const url = import.meta.env.VITE_AIRHAND_WS_URL
  const token = import.meta.env.VITE_AIRHAND_WS_TOKEN

  if (url && token) {
    return { ok: true, url, token }
  }

  if (!isTauriRuntime()) {
    return {
      ok: false,
      reason: 'no-dev-override',
      message:
        'No engine endpoint. A browser cannot read the handshake file, so development needs ' +
        'VITE_AIRHAND_WS_URL and VITE_AIRHAND_WS_TOKEN in .env.local, with the engine started ' +
        'using matching --port and --token flags.',
    }
  }

  return {
    ok: false,
    reason: 'tauri-not-available',
    message:
      'Handshake-file discovery is not implemented yet. The Tauri Rust layer must read ' +
      'runtime.json, verify the pid is alive, and hand the port and token to the webview.',
  }
}
