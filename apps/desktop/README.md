# AirHand Mouse — desktop app

React + TypeScript + Vite, wrapped in a Tauri v2 shell. The app contains **no computer-vision
logic**: every landmark, threshold and filter lives in the Python engine, and this side is a
consumer of the WebSocket contract in `shared/protocol/`.

## Running it

Two processes, always. The engine is a separate program by design and must stay runnable on its
own — Tauri does not spawn it yet.

```powershell
# terminal 1 — the engine
cd ..\..\backend
.\.venv\Scripts\python.exe -m airhand.main

# terminal 2 — the desktop shell
npm run tauri dev
```

The engine publishes `%LOCALAPPDATA%\AirHand\runtime.json` as it binds an ephemeral port, and the
Rust layer (`src-tauri/src/handshake.rs`) reads it, checks the publishing process is still alive,
and hands the port and token to the webview. Nothing has to be pinned or copied by hand.

### Browser development

`npm run dev` serves the same UI in a browser, which is faster to iterate on but **cannot read the
handshake file**. That path needs the endpoint pinned on both sides:

```powershell
# terminal 1
.\.venv\Scripts\python.exe -m airhand.main --port 8765 --token dev-token
```

```dotenv
# apps/desktop/.env.local
VITE_AIRHAND_WS_URL=ws://127.0.0.1:8765
VITE_AIRHAND_WS_TOKEN=dev-token
```

Inside Tauri the handshake wins over these variables; they are only a fallback for when there is
no usable handshake.

## Checks

Run in this order — each one is cheaper than the next, so fail fast:

```powershell
npx tsc --noEmit -p tsconfig.app.json
npm run lint          # oxlint, not eslint
npm run test          # vitest
npm run build
cd src-tauri; cargo test
```
