//! Reading the engine's handshake file.
//!
//! The engine binds an ephemeral loopback port and publishes where it landed. A browser cannot
//! read that file, which is the whole reason this Rust layer exists — see
//! `shared/protocol/README.md` → Discovery.
//!
//! Two rules shape everything below:
//!
//! - **A stale handshake is expected, not exceptional.** A hard kill leaves the file behind and
//!   the engine gets no cleanup opportunity, so every read verifies the `pid` is still running.
//!   Handing the webview a dead port instead produces a connection that hangs rather than fails.
//! - **The failure has to be named.** "No engine has been started" and "an engine died without
//!   cleaning up" are different problems with different fixes, and the user is the one who has to
//!   tell them apart.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// Mirror of `APP_DIR_NAME` / `HANDSHAKE_FILENAME` in `backend/airhand/handshake.py`.
///
/// Deliberately **not** `tauri::Manager::path().app_data_dir()`: that resolves to
/// `%APPDATA%\<identifier>`, a different directory from the one Python writes to, and the two
/// would drift apart silently — the reader would simply never find a file that exists.
const APP_DIR_NAME: &str = "AirHand";
const HANDSHAKE_FILENAME: &str = "runtime.json";

/// What the engine published. Field names match the JSON the Python side writes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Handshake {
    pub pid: u32,
    pub port: u16,
    pub protocol_version: String,
    pub token: String,
    /// Optional on purpose: it is diagnostic, and a handshake without it is still usable.
    #[serde(default)]
    pub started_at: Option<String>,
}

/// Named reasons, kebab-cased on the wire so they can be used directly as discovery failure codes
/// in `apps/desktop/src/lib/discovery.ts`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum FailureReason {
    HandshakeMissing,
    HandshakeUnreadable,
    HandshakeStale,
    /// We tried to start an engine and could not — a missing bundle, a refused spawn, or a
    /// process that never published a handshake. Distinct from the three above, which describe a
    /// file we found.
    EngineUnavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HandshakeFailure {
    pub reason: FailureReason,
    pub message: String,
}

impl HandshakeFailure {
    pub(crate) fn new(reason: FailureReason, message: impl Into<String>) -> Self {
        Self {
            reason,
            message: message.into(),
        }
    }
}

/// Per-user runtime directory, mirroring `app_data_dir()` on the Python side platform for
/// platform. Kept in step by hand; there is no way to share it between the two languages.
pub fn default_handshake_path() -> Option<PathBuf> {
    let base = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .or_else(|| home_dir().map(|home| home.join("AppData").join("Local")))
    } else if cfg!(target_os = "macos") {
        home_dir().map(|home| home.join("Library").join("Application Support"))
    } else {
        std::env::var_os("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .or_else(|| home_dir().map(|home| home.join(".local").join("state")))
    };

    base.map(|base| base.join(APP_DIR_NAME).join(HANDSHAKE_FILENAME))
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)
}

/// Read and validate a handshake file.
///
/// Liveness is injected rather than called directly so the stale path is testable without
/// spawning and killing a real process.
pub fn read_handshake_at(
    path: &Path,
    is_alive: &dyn Fn(u32) -> bool,
) -> Result<Handshake, HandshakeFailure> {
    let raw = match fs::read_to_string(path) {
        Ok(raw) => raw,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Err(HandshakeFailure::new(
                FailureReason::HandshakeMissing,
                format!(
                    "No engine handshake at {}. Start the AirHand engine — it publishes the file \
                     as it binds its port.",
                    path.display()
                ),
            ));
        }
        Err(error) => {
            return Err(HandshakeFailure::new(
                FailureReason::HandshakeUnreadable,
                format!("Cannot read {}: {error}", path.display()),
            ));
        }
    };

    // The write is atomic on the Python side (temp file + os.replace), so a malformed file is a
    // real problem — never a half-written one caught mid-flight.
    let handshake: Handshake = serde_json::from_str(&raw).map_err(|error| {
        HandshakeFailure::new(
            FailureReason::HandshakeUnreadable,
            format!("{} is not a valid handshake: {error}", path.display()),
        )
    })?;

    if !is_alive(handshake.pid) {
        return Err(HandshakeFailure::new(
            FailureReason::HandshakeStale,
            format!(
                "The handshake at {} belongs to process {}, which is no longer running. The \
                 engine was killed before it could clean up. Start it again.",
                path.display(),
                handshake.pid
            ),
        ));
    }

    Ok(handshake)
}

/// The real thing: default path, real process liveness.
pub fn read_handshake() -> Result<Handshake, HandshakeFailure> {
    let path = default_handshake_path().ok_or_else(|| {
        HandshakeFailure::new(
            FailureReason::HandshakeUnreadable,
            "Cannot locate the per-user application data directory.",
        )
    })?;

    read_handshake_at(&path, &process_is_alive)
}

/// Is a process with this pid running?
///
/// Caveat worth knowing: pids are reused. A handshake left by a dead engine whose pid has since
/// been handed to an unrelated process reads as live here. The connection then fails on the token
/// instead, which is a worse message but not a hang.
#[cfg(windows)]
pub fn process_is_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError};
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    // From ntstatus.h. Used as a sentinel exit code, which means a process that genuinely exits
    // with 259 reads as running — a documented Win32 wart, and not one this engine can hit.
    const STILL_ACTIVE: u32 = 259;
    const ERROR_ACCESS_DENIED: u32 = 5;

    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            // A process we are not allowed to open still exists. Reporting it dead would delete a
            // working handshake; reporting it alive costs at worst one honest connection failure.
            return GetLastError() == ERROR_ACCESS_DENIED;
        }

        let mut code: u32 = 0;
        let queried = GetExitCodeProcess(handle, &mut code);
        CloseHandle(handle);

        queried != 0 && code == STILL_ACTIVE
    }
}

/// The macOS/Linux port has to implement this before it can be trusted.
///
/// Until then every pid reads as live, which degrades to the pre-handshake behaviour — connect,
/// and let the socket report the failure — rather than to discarding a valid handshake.
#[cfg(not(windows))]
pub fn process_is_alive(_pid: u32) -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::cell::RefCell;
    use std::fs::File;
    use std::io::Write;

    use tempfile::tempdir;

    const VALID: &str = r#"{
        "pid": 4242,
        "port": 51873,
        "protocolVersion": "1.9.1",
        "token": "a-token",
        "startedAt": "2026-08-11T10:15:00Z"
    }"#;

    fn write(dir: &Path, contents: &str) -> PathBuf {
        let path = dir.join(HANDSHAKE_FILENAME);
        let mut file = File::create(&path).expect("create handshake");
        file.write_all(contents.as_bytes()).expect("write handshake");
        path
    }

    fn alive(_pid: u32) -> bool {
        true
    }

    fn dead(_pid: u32) -> bool {
        false
    }

    #[test]
    fn a_published_handshake_is_read_whole() {
        let dir = tempdir().expect("tempdir");
        let path = write(dir.path(), VALID);

        let handshake = read_handshake_at(&path, &alive).expect("handshake");

        assert_eq!(handshake.pid, 4242);
        assert_eq!(handshake.port, 51873);
        assert_eq!(handshake.protocol_version, "1.9.1");
        assert_eq!(handshake.token, "a-token");
        assert_eq!(handshake.started_at.as_deref(), Some("2026-08-11T10:15:00Z"));
    }

    #[test]
    fn liveness_is_asked_about_the_pid_from_the_file() {
        let dir = tempdir().expect("tempdir");
        let path = write(dir.path(), VALID);
        let asked = RefCell::new(Vec::new());

        let _ = read_handshake_at(&path, &|pid| {
            asked.borrow_mut().push(pid);
            true
        });

        assert_eq!(asked.into_inner(), vec![4242]);
    }

    #[test]
    fn no_file_means_missing_not_unreadable() {
        let dir = tempdir().expect("tempdir");

        let failure = read_handshake_at(&dir.path().join(HANDSHAKE_FILENAME), &alive)
            .expect_err("no engine has run");

        assert_eq!(failure.reason, FailureReason::HandshakeMissing);
    }

    #[test]
    fn a_file_that_is_not_json_is_unreadable() {
        let dir = tempdir().expect("tempdir");
        let path = write(dir.path(), "not json at all");

        let failure = read_handshake_at(&path, &alive).expect_err("garbage");

        assert_eq!(failure.reason, FailureReason::HandshakeUnreadable);
    }

    #[test]
    fn a_handshake_without_a_token_is_unreadable() {
        let dir = tempdir().expect("tempdir");
        let path = write(
            dir.path(),
            r#"{"pid": 1, "port": 8765, "protocolVersion": "1.9.1"}"#,
        );

        let failure = read_handshake_at(&path, &alive).expect_err("no token");

        assert_eq!(failure.reason, FailureReason::HandshakeUnreadable);
    }

    #[test]
    fn a_handshake_from_a_dead_process_is_stale() {
        let dir = tempdir().expect("tempdir");
        let path = write(dir.path(), VALID);

        let failure = read_handshake_at(&path, &dead).expect_err("killed engine");

        assert_eq!(failure.reason, FailureReason::HandshakeStale);
        // The message has to say which process, because the next step is checking whether it is
        // really gone.
        assert!(failure.message.contains("4242"));
    }

    #[test]
    fn this_process_is_alive_by_the_real_check() {
        assert!(process_is_alive(std::process::id()));
    }

    #[test]
    fn the_default_path_ends_where_python_writes() {
        let path = default_handshake_path().expect("a home directory");

        assert!(path.ends_with(Path::new(APP_DIR_NAME).join(HANDSHAKE_FILENAME)));
    }
}
