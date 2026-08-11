# AirHand Mouse — desktop app

React + TypeScript + Vite, wrapped in a Tauri v2 shell. The app contains **no computer-vision
logic**: every landmark, threshold and filter lives in the Python engine, and this side is a
consumer of the WebSocket contract in `shared/protocol/`.

## Running it

One command, once the engine has been frozen:

```powershell
cd ..\..\backend; .\.venv\Scripts\pyinstaller.exe airhand.spec   # once, and after engine changes
cd ..\apps\desktop; npm run engine:sync                          # copies it into src-tauri/engine
npm run tauri dev
```

The shell starts the engine, waits for it to publish
`%LOCALAPPDATA%\AirHand\runtime.json`, and hands the port and token to the webview
(`src-tauri/src/engine.rs`, `src-tauri/src/handshake.rs`).

**Unless one is already running.** A live handshake is adopted rather than replaced: two engines
fight over the camera, and the second one's handshake overwrites the first's. So the old shape
still works and is still the faster loop while changing Python —

```powershell
# terminal 1
cd ..\..\backend; .\.venv\Scripts\python.exe -m airhand.main

# terminal 2
npm run tauri dev
```

— and closing the window leaves that engine running, because the app only stops what it started.

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

`cargo` needs `src-tauri/engine/` to exist — `bundle.resources` fails the build on a glob that
matches nothing. Run `npm run engine:sync` first.
