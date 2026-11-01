// VESPER Studio for macOS and Windows (Lab spec §7, plan W6): a native window around VESPER's own lobby.
//
// The app starts `vesper studio` (on Windows inside WSL2, where Habitat and Home Assistant run), waits until its
// page answers on 127.0.0.1, and shows it. Everything else is VESPER's: the app adds no API to the page. When the
// window closes, VESPER is asked to stop (SIGTERM), so the home it is playing stops too.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Url};

const PORT: u16 = 18080;
const START_TIMEOUT: Duration = Duration::from_secs(90);

struct Server(Mutex<Option<Child>>);

fn home() -> PathBuf {
    std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")).map(PathBuf::from).unwrap_or_default()
}

/// The lobby already answers (a `vesper studio` started by hand, or by an earlier window).
fn lobby_up() -> bool {
    let Ok(mut s) = TcpStream::connect_timeout(&([127, 0, 0, 1], PORT).into(), Duration::from_millis(400)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_secs(2)));
    let req = format!("GET /api/homes HTTP/1.0\r\nHost: 127.0.0.1:{PORT}\r\n\r\n");
    let mut buf = [0u8; 12];
    s.write_all(req.as_bytes()).is_ok() && s.read_exact(&mut buf).is_ok() && buf.starts_with(b"HTTP/1.0 200")
}

/// `vesper` on this machine: $VESPER_BIN, the usual install places, then a login shell's PATH (an app opened from
/// the Finder gets a minimal PATH).
#[cfg(not(windows))]
fn find_vesper() -> Option<PathBuf> {
    let mut places: Vec<PathBuf> = std::env::var_os("VESPER_BIN").map(PathBuf::from).into_iter().collect();
    let h = home();
    places.extend([h.join(".local/bin/vesper"), PathBuf::from("/opt/homebrew/bin/vesper"),
                   PathBuf::from("/usr/local/bin/vesper")]);
    if let Some(p) = places.into_iter().find(|p| p.is_file()) {
        return Some(p);
    }
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".into());
    let out = Command::new(shell).args(["-lc", "command -v vesper"]).output().ok()?;
    let found = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (out.status.success() && !found.is_empty()).then(|| PathBuf::from(found))
}

#[cfg(not(windows))]
fn start_vesper() -> Result<Child, String> {
    let vesper = find_vesper().ok_or("VESPER is not installed (no `vesper` command found)")?;
    Command::new(&vesper)
        .args(["studio", "--no-browser", "--port", &PORT.to_string()])
        .current_dir(home())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("{} could not start: {e}", vesper.display()))
}

/// On Windows VESPER runs in WSL2 (Habitat and Home Assistant have no native Windows build); WSL2 forwards its
/// 127.0.0.1 ports to Windows.
#[cfg(windows)]
fn start_vesper() -> Result<Child, String> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    Command::new("wsl.exe")
        .args(["--", "bash", "-lc", &format!("cd ~ && exec vesper studio --no-browser --port {PORT}")])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("WSL could not start VESPER: {e} (install WSL2, then VESPER inside it)"))
}

fn stop(child: &mut Child) {
    #[cfg(unix)]
    {
        let _ = Command::new("kill").args(["-TERM", &child.id().to_string()]).status();
        let end = Instant::now() + Duration::from_secs(8);
        while Instant::now() < end {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn show_error(app: &tauri::AppHandle, text: &str) {
    if let Some(w) = app.get_webview_window("main") {
        let quoted = format!("{text:?}"); // a JSON-compatible string literal
        let _ = w.eval(&format!("window.vesperFailed && window.vesperFailed({quoted})"));
    }
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Server(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            thread::spawn(move || {
                if !lobby_up() {
                    match start_vesper() {
                        Ok(child) => *handle.state::<Server>().0.lock().unwrap() = Some(child),
                        Err(e) => return show_error(&handle, &e),
                    }
                }
                let end = Instant::now() + START_TIMEOUT;
                while Instant::now() < end {
                    if lobby_up() {
                        if let (Some(w), Ok(url)) = (handle.get_webview_window("main"),
                                                     Url::parse(&format!("http://127.0.0.1:{PORT}/"))) {
                            let _ = w.navigate(url);
                        }
                        return;
                    }
                    let exited = handle.state::<Server>().0.lock().unwrap().as_mut()
                        .map(|c| matches!(c.try_wait(), Ok(Some(_)))).unwrap_or(false);
                    if exited {
                        return show_error(&handle, "VESPER stopped before its page opened. In a terminal, run \
                                                    `vesper studio` to see why (often: the catalog is missing; \
                                                    `vesper catalog fetch` or `vesper catalog build`).");
                    }
                    thread::sleep(Duration::from_millis(250));
                }
                show_error(&handle, "VESPER's page did not open within 90 seconds.");
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("VESPER Studio could not start");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Server>().0.lock().unwrap().take() {
                stop(&mut child);
            }
        }
    });
}
