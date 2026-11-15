//! Tauri shell: owns the Python backend sidecar.
//!
//! The PyInstaller `--onedir` build is shipped as a Tauri *resource* folder
//! (`<install dir>/backend/videostudio-backend.exe` + `_internal/`) and launched
//! with `std::process::Command`. The backend binds a free port on 127.0.0.1
//! (`--port 0`) and prints `VIDEOSTUDIO_PORT=<port>` on stdout; we read it and
//! hand `{ base_url, token }` to the UI through the `backend_info` command.
//!
//! Dev mode: set `VIDEOSTUDIO_BACKEND_URL` (+ optional `VIDEOSTUDIO_BACKEND_TOKEN`)
//! to use a backend started separately (with auto-reload) instead of the sidecar.
//!
//! Tray, close-to-tray, autostart and notifications live in [`desktop`].

mod desktop;

use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

use rand::{distributions::Alphanumeric, Rng};
use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

use desktop::{Desktop, AUTOSTART_ARG};

const PORT_PREFIX: &str = "VIDEOSTUDIO_PORT=";
const READY_TIMEOUT: Duration = Duration::from_secs(90);

#[derive(Clone, Serialize)]
struct BackendInfo {
    base_url: String,
    token: String,
}

#[derive(Default)]
pub(crate) struct Backend {
    info: Mutex<Option<Result<BackendInfo, String>>>,
    ready: Condvar,
    child: Mutex<Option<Child>>,
}

impl Backend {
    fn set_info(&self, value: Result<BackendInfo, String>) {
        let mut info = self.info.lock().unwrap();
        if info.is_none() {
            *info = Some(value);
            self.ready.notify_all();
        }
    }

    pub(crate) fn wait_info(&self, timeout: Duration) -> Result<BackendInfo, String> {
        let guard = self.info.lock().unwrap();
        let (guard, _) = self
            .ready
            .wait_timeout_while(guard, timeout, |info| info.is_none())
            .unwrap();
        guard
            .clone()
            .unwrap_or_else(|| Err("Backend did not start in time".into()))
    }

    fn kill(&self) {
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[tauri::command]
async fn backend_info(state: State<'_, Arc<Backend>>) -> Result<BackendInfo, String> {
    let backend = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || backend.wait_info(READY_TIMEOUT))
        .await
        .map_err(|e| e.to_string())?
}

fn random_token() -> String {
    rand::thread_rng()
        .sample_iter(&Alphanumeric)
        .take(48)
        .map(char::from)
        .collect()
}

fn sidecar_exe(resource_dir: &std::path::Path) -> Result<PathBuf, String> {
    if let Ok(custom) = std::env::var("VIDEOSTUDIO_SIDECAR") {
        return Ok(PathBuf::from(custom));
    }
    let name = if cfg!(windows) {
        "videostudio-backend.exe"
    } else {
        "videostudio-backend"
    };
    let exe = resource_dir.join("backend").join(name);
    if exe.is_file() {
        Ok(exe)
    } else {
        Err(format!(
            "Không tìm thấy backend sidecar tại {}. Hãy chạy scripts/build-sidecar để tạo, \
             hoặc đặt VIDEOSTUDIO_BACKEND_URL khi phát triển.",
            exe.display()
        ))
    }
}

fn forward_output<R: Read + Send + 'static>(stream: R, label: &'static str) {
    thread::spawn(move || {
        for line in BufReader::new(stream).lines().map_while(Result::ok) {
            eprintln!("[backend {label}] {line}");
        }
    });
}

fn spawn_sidecar(app: &AppHandle, backend: Arc<Backend>) -> Result<(), String> {
    let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
    let exe = sidecar_exe(&resource_dir)?;
    let token = random_token();

    let mut cmd = Command::new(&exe);
    cmd.arg("--port")
        .arg("0")
        .env("VIDEOSTUDIO_TOKEN", &token)
        .env("VIDEOSTUDIO_PARENT_PID", std::process::id().to_string())
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(dir) = exe.parent() {
        cmd.current_dir(dir);
    }
    let ffmpeg_dir = resource_dir.join("ffmpeg");
    if ffmpeg_dir.is_dir() {
        cmd.env("VIDEOSTUDIO_FFMPEG_DIR", ffmpeg_dir);
    }
    let tesseract_dir = resource_dir.join("tesseract");
    if tesseract_dir.is_dir() {
        cmd.env("VIDEOSTUDIO_TESSERACT_DIR", tesseract_dir);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Không chạy được backend {}: {e}", exe.display()))?;
    let stdout = child.stdout.take().expect("piped stdout");
    if let Some(stderr) = child.stderr.take() {
        forward_output(stderr, "stderr");
    }
    *backend.child.lock().unwrap() = Some(child);

    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(port) = line.trim().strip_prefix(PORT_PREFIX) {
                backend.set_info(Ok(BackendInfo {
                    base_url: format!("http://127.0.0.1:{}", port.trim()),
                    token: token.clone(),
                }));
            }
            eprintln!("[backend stdout] {line}");
        }
        backend.set_info(Err("Backend đã thoát trước khi sẵn sàng".into()));
    });
    Ok(())
}

pub fn run() {
    let backend = Arc::new(Backend::default());
    let setup_backend = backend.clone();

    let app = tauri::Builder::default()
        // Must be the first plugin: a second launch (e.g. autostart while running)
        // just brings the existing window forward.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            desktop::show_main(app);
        }))
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .arg(AUTOSTART_ARG)
                .build(),
        )
        .manage(backend.clone())
        .manage(Desktop::default())
        .invoke_handler(tauri::generate_handler![
            backend_info,
            desktop::apply_desktop_prefs,
            desktop::set_tray_paused,
            desktop::desktop_notify,
            desktop::open_external,
            desktop::facebook_login,
        ])
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            if let WindowEvent::CloseRequested { api, .. } = event {
                let app = window.app_handle();
                if desktop::close_to_tray(&app.state::<Desktop>()) {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(move |app| {
            let prefs = desktop::load_prefs(app.handle());
            let autostarted = std::env::args().any(|a| a == AUTOSTART_ARG);
            if autostarted && prefs.start_minimized {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.hide();
                }
            }
            *app.state::<Desktop>().prefs.lock().unwrap() = prefs;
            if let Err(err) = desktop::setup_tray(app.handle()) {
                eprintln!("tray icon unavailable: {err}");
            }
            if let Ok(url) = std::env::var("VIDEOSTUDIO_BACKEND_URL") {
                let token = std::env::var("VIDEOSTUDIO_BACKEND_TOKEN")
                    .unwrap_or_else(|_| "dev-token".into());
                setup_backend.set_info(Ok(BackendInfo {
                    base_url: url,
                    token,
                }));
            } else if let Err(err) = spawn_sidecar(app.handle(), setup_backend.clone()) {
                eprintln!("{err}");
                setup_backend.set_info(Err(err));
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(move |_handle, event| {
        if let RunEvent::Exit = event {
            backend.kill();
        }
    });
}
