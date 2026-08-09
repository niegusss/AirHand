/// <reference types="vite/client" />

/** Injected by vite.config.ts from shared/protocol/protocol.json. */
declare const __PROTOCOL_VERSION__: string
/** Injected by vite.config.ts from shared/protocol/protocol.json. */
declare const __LANDMARK_COUNT__: number

interface ImportMetaEnv {
  /**
   * Development-only override for engine discovery. In the packaged app the endpoint comes from
   * the handshake file via Tauri; a browser cannot read that file.
   */
  readonly VITE_AIRHAND_WS_URL?: string
  /** Development-only token override; must match the engine's `--token` flag. */
  readonly VITE_AIRHAND_WS_TOKEN?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
