mod handshake;

use handshake::{Handshake, HandshakeFailure};

/// Hand the webview the engine's endpoint.
///
/// Returns the published fields **as they were written**. The protocol version is not compared
/// here: `protocol.json` is injected into the frontend at build time and read by Python at
/// runtime, and a third copy of that check in Rust is exactly the drift the single-source-of-truth
/// arrangement exists to prevent. See `apps/desktop/src/lib/discovery.ts`.
#[tauri::command]
fn read_handshake() -> Result<Handshake, HandshakeFailure> {
    let result = handshake::read_handshake();

    // Logged because a webview console is not somewhere a user can be asked to look. Never the
    // token: it is the one credential in this system.
    match &result {
        Ok(handshake) => log::info!(
            "Handshake: engine pid {} on port {}, protocol {}",
            handshake.pid,
            handshake.port,
            handshake.protocol_version
        ),
        Err(failure) => log::warn!("No usable handshake: {}", failure.message),
    }

    result
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }
      Ok(())
    })
    .invoke_handler(tauri::generate_handler![read_handshake])
    .run(tauri::generate_context!())
    .expect("error while running tauri application");
}
