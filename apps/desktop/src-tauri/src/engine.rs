//! The engine process: start it if nobody else has, and stop only what we started.
//!
//! The governing rule is the second half of that sentence. Two engines on one machine fight over
//! the camera and the loser's handshake overwrites the winner's, so this module never starts a
//! second one — and never kills one it did not start. That is also what keeps the development
//! shape alive: an engine in a terminal with the window on top of it, which is how every
//! measurement in this project has been taken.

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

use crate::handshake::{self, FailureReason, Handshake, HandshakeFailure};

/// Where the frozen engine lives inside the bundle, relative to the resource directory.
const ENGINE_RESOURCE: &str = "engine/airhand-engine.exe";

/// How long a freshly spawned engine gets to publish its handshake. Cold start measured at 0.87 s
/// for the one-directory build; ten seconds is room for a slow disk, not a guess at the budget.
const STARTUP_DEADLINE: Duration = Duration::from_secs(10);
const POLL_INTERVAL: Duration = Duration::from_millis(100);

/// The engine **we** started, if any.
///
/// An adopted engine is deliberately absent from this state. Storing it would invite killing it,
/// and it belongs to whoever launched it.
#[derive(Default)]
pub struct EngineState {
    child: Mutex<Option<CommandChild>>,
}

/// Return an endpoint for a running engine, starting one if there is none.
///
/// Blocking: it waits for a spawned process to publish its handshake. Call it off the main
/// thread.
pub fn ensure_running(app: &AppHandle) -> Result<Handshake, HandshakeFailure> {
    let state = app.state::<EngineState>();
    // Held across the whole spawn-and-wait. A second caller — React's StrictMode double-mount is
    // the everyday one — then finds the engine already up instead of starting a rival.
    let mut child = state
        .child
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());

    // Someone's engine is already up: a developer's terminal, or ours from an earlier call. Either
    // way it is the engine to talk to.
    if let Ok(handshake) = handshake::read_handshake() {
        return Ok(handshake);
    }

    // No usable handshake, so anything we started before is gone.
    *child = None;

    let executable = locate_engine(app)?;
    log::info!("Starting engine: {}", executable.display());

    let (mut events, spawned) = app
        .shell()
        .command(&executable)
        .spawn()
        .map_err(|error| {
            HandshakeFailure::new(
                FailureReason::EngineUnavailable,
                format!("Could not start {}: {error}", executable.display()),
            )
        })?;

    let pid = spawned.pid();
    *child = Some(spawned);

    // Drain the pipes, or the engine blocks on its own logging once the buffer fills — it prints
    // a throughput line every five seconds forever. Forwarding rather than discarding, because
    // this is the only place an engine failure inside the packaged app becomes visible.
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    log::info!("engine | {}", String::from_utf8_lossy(&line).trim_end());
                }
                CommandEvent::Terminated(payload) => {
                    log::warn!("Engine exited with {:?}", payload.code);
                }
                _ => {}
            }
        }
    });

    wait_for_child_handshake(pid, STARTUP_DEADLINE, POLL_INTERVAL, &|| {
        handshake::read_handshake()
    })
}

/// Kill the engine, but only if it is ours.
pub fn shutdown(app: &AppHandle) {
    let Some(state) = app.try_state::<EngineState>() else {
        return;
    };
    let child = state
        .child
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .take();

    if let Some(child) = child {
        log::info!("Stopping the engine we started");
        // Hard termination: the engine gets no chance to remove its handshake file. That is the
        // stale-handshake case the reader was built for, and re-publishing on the next launch
        // costs nothing.
        let _ = child.kill();
    }
}

fn locate_engine(app: &AppHandle) -> Result<PathBuf, HandshakeFailure> {
    let unavailable = |message: String| HandshakeFailure::new(FailureReason::EngineUnavailable, message);

    let path = app
        .path()
        .resolve(ENGINE_RESOURCE, BaseDirectory::Resource)
        .map_err(|error| unavailable(format!("Cannot resolve {ENGINE_RESOURCE}: {error}")))?;

    if !path.exists() {
        return Err(unavailable(format!(
            "No engine at {}. Build it with `pyinstaller airhand.spec` in backend/ and copy \
             dist/airhand-engine into src-tauri/engine (npm run engine:sync).",
            path.display()
        )));
    }

    Ok(path)
}

/// Wait for a handshake published by **this** process.
///
/// The pid check is the point. A handshake left behind by an earlier run is sitting at the same
/// path, and without it the first look would return someone else's dead endpoint before our own
/// engine had finished binding.
pub fn wait_for_child_handshake(
    child_pid: u32,
    deadline: Duration,
    poll: Duration,
    read: &dyn Fn() -> Result<Handshake, HandshakeFailure>,
) -> Result<Handshake, HandshakeFailure> {
    let started = Instant::now();

    loop {
        if let Ok(handshake) = read() {
            if handshake.pid == child_pid {
                return Ok(handshake);
            }
        }

        if started.elapsed() >= deadline {
            return Err(HandshakeFailure::new(
                FailureReason::EngineUnavailable,
                format!(
                    "The engine was started (process {child_pid}) but published no handshake \
                     within {} seconds. Its output is in the application log.",
                    deadline.as_secs()
                ),
            ));
        }

        std::thread::sleep(poll);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::cell::RefCell;

    fn handshake(pid: u32) -> Handshake {
        Handshake {
            pid,
            port: 51873,
            protocol_version: "1.9.1".to_string(),
            token: "a-token".to_string(),
            started_at: None,
        }
    }

    fn missing() -> HandshakeFailure {
        HandshakeFailure::new(FailureReason::HandshakeMissing, "not yet")
    }

    #[test]
    fn waits_until_the_handshake_appears() {
        let reads = RefCell::new(0);

        let result = wait_for_child_handshake(
            77,
            Duration::from_secs(5),
            Duration::from_millis(1),
            &|| {
                let mut reads = reads.borrow_mut();
                *reads += 1;
                if *reads < 3 {
                    Err(missing())
                } else {
                    Ok(handshake(77))
                }
            },
        )
        .expect("the engine came up");

        assert_eq!(result.pid, 77);
        assert_eq!(reads.into_inner(), 3);
    }

    #[test]
    fn ignores_a_handshake_belonging_to_someone_else() {
        // The file left by a previous run is at the same path and looks perfectly valid.
        let reads = RefCell::new(0);

        let result = wait_for_child_handshake(
            77,
            Duration::from_secs(5),
            Duration::from_millis(1),
            &|| {
                let mut reads = reads.borrow_mut();
                *reads += 1;
                Ok(handshake(if *reads < 4 { 12345 } else { 77 }))
            },
        )
        .expect("our own engine eventually published");

        assert_eq!(result.pid, 77);
    }

    #[test]
    fn gives_up_and_says_the_engine_is_unavailable() {
        let failure = wait_for_child_handshake(
            77,
            Duration::from_millis(30),
            Duration::from_millis(5),
            &|| Err(missing()),
        )
        .expect_err("nothing ever appeared");

        assert_eq!(failure.reason, FailureReason::EngineUnavailable);
        // Points at where the engine's own complaint went, since the process is invisible.
        assert!(failure.message.contains("application log"));
    }
}
