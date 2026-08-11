mod engine;
mod handshake;

use handshake::{FailureReason, Handshake, HandshakeFailure};
use tauri::{AppHandle, RunEvent};

/// Hand the webview an endpoint for a running engine, starting one if there is none.
///
/// Returns the published fields **as they were written**. The protocol version is not compared
/// here: `protocol.json` is injected into the frontend at build time and read by Python at
/// runtime, and a third copy of that check in Rust is exactly the drift the single-source-of-truth
/// arrangement exists to prevent. See `apps/desktop/src/lib/discovery.ts`.
///
/// Async, and the work happens on a blocking thread: starting an engine means waiting for it to
/// bind a port, and a synchronous command would spend that time freezing the window.
#[tauri::command]
async fn read_handshake(app: AppHandle) -> Result<Handshake, HandshakeFailure> {
    tauri::async_runtime::spawn_blocking(move || {
        let result = engine::ensure_running(&app);

        // Logged because a webview console is not somewhere a user can be asked to look. Never the
        // token: it is the one credential in this system.
        match &result {
            Ok(handshake) => log::info!(
                "Handshake: engine pid {} on port {}, protocol {}",
                handshake.pid,
                handshake.port,
                handshake.protocol_version
            ),
            Err(failure) => log::warn!("No usable engine: {}", failure.message),
        }

        result
    })
    .await
    .unwrap_or_else(|error| {
        Err(HandshakeFailure::new(
            FailureReason::EngineUnavailable,
            format!("The engine lookup did not finish: {error}"),
        ))
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  let app = tauri::Builder::default()
    .plugin(tauri_plugin_shell::init())
    .manage(engine::EngineState::default())
    .setup(|app| {
      // Registered in release too, and that is the point. The scaffold gated this on
      // debug_assertions, which switched the log off in the only build where nobody can see a
      // console: the engine's own output is forwarded here, and a packaged app with no log is a
      // failure nobody can diagnose. Targets are stdout and the app's log directory.
      app.handle().plugin(
        tauri_plugin_log::Builder::default()
          .level(log::LevelFilter::Info)
          .build(),
      )?;
      Ok(())
    })
    .invoke_handler(tauri::generate_handler![read_handshake])
    .build(tauri::generate_context!())
    .expect("error while building tauri application");

  // The engine outlives the window unless someone ends it: it is a separate process that holds a
  // camera and can drive the cursor. Whatever we started, we stop.
  app.run(|handle, event| {
    if matches!(event, RunEvent::Exit) {
      engine::shutdown(handle);
    }
  });
}
