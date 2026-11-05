// VESPER Studio for macOS and Windows (Lab spec §7, plan W6): a native window around VESPER's own lobby.
//
// The app starts `vesper studio` (on Windows inside WSL2, where Habitat and Home Assistant run), waits until its
// page answers on 127.0.0.1, and shows it. Everything else is VESPER's: the app adds no API to the page. A played home
// opens in the default browser, where mouse look works. When the window closes, VESPER is asked to stop (SIGTERM),
// so the home it is playing stops too.
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
struct LastError(Mutex<Option<String>>); // re-sent when the start page loads: an early error is never lost

/// Where VESPER's output goes (it was discarded): when VESPER stops before its page opens, its last line says why.
fn log_path() -> PathBuf {
    std::env::temp_dir().join("vesper-studio.log")
}

/// The output's last non-empty line, at most 400 characters: VESPER ends a failed start with one line saying why
/// (a missing Python package, a catalog, a port), or a traceback whose last line names the error.
fn last_line(text: &str) -> Option<String> {
    let line = text.lines().rev().map(str::trim).find(|l| !l.is_empty())?;
    Some(line.chars().take(400).collect())
}

/// Stdout and stderr for VESPER: the log file (both into it), or nowhere if it cannot be made.
fn log_stdio() -> (Stdio, Stdio) {
    match std::fs::File::create(log_path()) {
        Ok(f) => match f.try_clone() {
            Ok(g) => (Stdio::from(f), Stdio::from(g)),
            Err(_) => (Stdio::null(), Stdio::from(f)),
        },
        Err(_) => (Stdio::null(), Stdio::null()),
    }
}

/// The message shown when VESPER stops before its page opens.
fn stopped_message() -> String {
    let said = std::fs::read_to_string(log_path()).ok().as_deref().and_then(last_line);
    let base = "VESPER stopped before its page opened.";
    let causes = "Common causes: a Python package missing from an old install (reinstall from the VESPER folder: \
                  `uv tool install --python 3.12 --editable '.[scan]' --force`), a missing catalog (`vesper catalog \
                  fetch` or `build`), port 18080 held by another program, or VESPER missing inside WSL on Windows.";
    match said {
        Some(line) => format!("{base} It said: {line} (full output: {}). {causes}", log_path().display()),
        None => format!("{base} In a terminal, run `vesper studio` to see why. {causes}"),
    }
}

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
    use std::os::unix::process::CommandExt;
    let vesper = find_vesper().ok_or("VESPER is not installed (no `vesper` command found)")?;
    let (out, err) = log_stdio();
    Command::new(&vesper)
        .args(["studio", "--no-browser", "--port", &PORT.to_string()])
        .current_dir(home())
        .process_group(0) // its own group: a SIGTERM reaches a wrapper script and the python under it alike
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err)
        .spawn()
        .map_err(|e| format!("{} could not start: {e}", vesper.display()))
}

/// On Windows VESPER runs in WSL2 (Habitat and Home Assistant have no native Windows build); WSL2 forwards its
/// 127.0.0.1 ports to Windows. Ending wsl.exe does not end the Linux process, so VESPER writes its pid there and is
/// stopped by it.
#[cfg(windows)]
const WSL_PID: &str = "~/.vesper-studio.pid";

#[cfg(windows)]
fn wsl(script: &str) -> Command {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let mut c = Command::new("wsl.exe");
    c.args(["--", "bash", "-lc", script]).creation_flags(CREATE_NO_WINDOW);
    c
}

#[cfg(windows)]
fn start_vesper() -> Result<Child, String> {
    let (out, err) = log_stdio();
    wsl(&format!("cd ~ && echo $$ > {WSL_PID} && exec vesper studio --no-browser --port {PORT}"))
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err)
        .spawn()
        .map_err(|e| format!("WSL could not start VESPER: {e} (install WSL2, then VESPER inside it)"))
}

fn stop(child: &mut Child) {
    #[cfg(unix)]
    {
        let _ = Command::new("kill").args(["-TERM", &format!("-{}", child.id())]).status(); // the whole group
    }
    #[cfg(windows)]
    {
        let _ = wsl(&format!("kill -TERM $(cat {WSL_PID}) 2>/dev/null; rm -f {WSL_PID}")).status();
    }
    let end = Instant::now() + Duration::from_secs(8);
    while Instant::now() < end {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// Whether a page stays in the app's window: the bundled start page and VESPER's lobby (with its compose page). A played
/// home opens in the default browser instead, where the 3D view can capture the mouse and look around as games do; the
/// app's web view gives a page no pointer lock on macOS.
fn stays_in_window(url: &Url) -> bool {
    match url.scheme() {
        "http" | "https" => url.host_str() == Some("127.0.0.1") && url.port() == Some(PORT),
        _ => true, // tauri:// and the like: the bundled start page
    }
}

fn open_in_browser(url: &Url) {
    #[cfg(target_os = "macos")]
    let _ = Command::new("open").arg(url.as_str()).spawn();
    #[cfg(windows)]
    let _ = Command::new("cmd").args(["/C", "start", "", url.as_str()]).spawn(); // WSL2 forwards the port
    #[cfg(all(unix, not(target_os = "macos")))]
    let _ = Command::new("xdg-open").arg(url.as_str()).spawn();
}

fn show_error(app: &tauri::AppHandle, text: &str) {
    *app.state::<LastError>().0.lock().unwrap() = Some(text.to_string());
    send_error(app);
}

fn send_error(app: &tauri::AppHandle) {
    let last = app.state::<LastError>().0.lock().unwrap().clone();
    if let (Some(text), Some(w)) = (last, app.get_webview_window("main")) {
        let quoted = format!("{text:?}"); // a JSON-compatible string literal
        let _ = w.eval(&format!("window.vesperFailed && window.vesperFailed({quoted})"));
    }
}

fn main() {
    let browser = tauri::plugin::Builder::<tauri::Wry>::new("browser")
        .on_navigation(|_webview, url| {
            if stays_in_window(url) {
                return true;
            }
            open_in_browser(url);
            false
        })
        .build();
    let app = tauri::Builder::default()
        .plugin(browser)
        .manage(Server(Mutex::new(None)))
        .manage(LastError(Mutex::new(None)))
        .on_page_load(|webview, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
                send_error(webview.app_handle());
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            thread::spawn(move || {
                if !lobby_up() {
                    match start_vesper() {
                        Ok(child) => *handle.state::<Server>().0.lock().unwrap() = Some(child),
                        Err(e) => return show_error(&handle, &e),
                    }
                }
                let start = Instant::now();
                let mut told = false;
                loop {
                    if lobby_up() {
                        *handle.state::<LastError>().0.lock().unwrap() = None;
                        if let (Some(w), Ok(url)) = (handle.get_webview_window("main"),
                                                     Url::parse(&format!("http://127.0.0.1:{PORT}/"))) {
                            let _ = w.navigate(url);
                        }
                        return;
                    }
                    let exited = handle.state::<Server>().0.lock().unwrap().as_mut()
                        .map(|c| matches!(c.try_wait(), Ok(Some(_)))).unwrap_or(false);
                    if exited {
                        return show_error(&handle, &stopped_message());
                    }
                    if !told && start.elapsed() > START_TIMEOUT {
                        told = true; // keep waiting: a first start in WSL, or a large catalog, can be slow
                        show_error(&handle, "VESPER is still starting (over 90 seconds); this window opens it \
                                             as soon as it answers.");
                    }
                    thread::sleep(Duration::from_millis(250));
                }
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

#[cfg(test)]
mod tests {
    use super::last_line;

    #[test]
    fn the_last_line_is_the_reason_vesper_gave() {
        let out = "Traceback (most recent call last):\n  File x\nModuleNotFoundError: No module named 'astral'\n\n";
        assert_eq!(last_line(out).as_deref(), Some("ModuleNotFoundError: No module named 'astral'"));
        assert_eq!(last_line("VESPER cannot start: the Python package 'astral' is missing\n").as_deref(),
                   Some("VESPER cannot start: the Python package 'astral' is missing"));
        assert_eq!(last_line("\n  \n"), None);
        assert_eq!(last_line(&"x".repeat(1000)).map(|l| l.len()), Some(400));
    }
}
