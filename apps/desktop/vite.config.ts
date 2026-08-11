import { readFileSync } from 'node:fs'
import path from 'node:path'
// From `vitest/config`, not `vite`: it is a superset, and sharing one config is what makes the
// tests see the same `@` alias and the same injected protocol constants as the app. A separate
// vitest.config.ts would have to repeat the injection below — the drift this whole mechanism
// exists to prevent.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const root = import.meta.dirname

/*
 * The protocol version and landmark count come from shared/protocol/protocol.json — the same file
 * the Python engine reads — and are injected at build time. Mirroring them by hand in TypeScript
 * is exactly the drift systemPatterns.md forbids.
 */
const protocolSpec = JSON.parse(
  readFileSync(path.resolve(root, '../../shared/protocol/protocol.json'), 'utf-8'),
) as { version: string; landmarkCount: number }

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // `tauri dev` starts this server itself and then points the webview at a fixed URL, so the port
  // is part of the contract in tauri.conf.json rather than a convenience. Without strictPort, a
  // busy 5173 silently moves the dev server and the window comes up blank.
  server: {
    port: 5173,
    strictPort: true,
  },
  // Tauri's own output has to stay on screen; Vite clearing it hides the Rust build errors.
  clearScreen: false,
  envPrefix: ['VITE_', 'TAURI_ENV_'],
  build: {
    // The only browser this ships to is WebView2 (Chromium). Transpiling for older engines buys
    // nothing and costs bundle size.
    target: 'chrome105',
  },
  define: {
    __PROTOCOL_VERSION__: JSON.stringify(protocolSpec.version),
    __LANDMARK_COUNT__: JSON.stringify(protocolSpec.landmarkCount),
  },
  resolve: {
    alias: {
      '@': path.resolve(root, './src'),
    },
  },
  test: {
    // Node, not jsdom: what is under test is plain TypeScript — wire parsing and store logic.
    // jsdom and @testing-library go in with the first component test, not before it.
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
