//! Background running: tray icon, close-to-tray, autostart, notifications, the
//! Facebook login window and opening links in the default browser.
//!
//! Preferences are owned by the backend (`/settings/desktop`); the UI pushes them
//! here with `apply_desktop_prefs` and we cache them in `desktop.json` so they are
//! known at the next launch before the UI has loaded (e.g. start minimized).

use std::fs;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::{mpsc, Mutex};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, Runtime, State, WebviewUrl, WebviewWindowBuilder, Wry};
use tauri_plugin_autostart::ManagerExt as _;
use tauri_plugin_notification::NotificationExt as _;

use crate::Backend;

pub const AUTOSTART_ARG: &str = "--autostart";
const FB_LOGIN_LABEL: &str = "facebook-login";
const FB_SUCCESS_PATH: &str = "/connect/login_success.html";
const FB_LOGIN_TIMEOUT: Duration = Duration::from_secs(15 * 60);

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
pub struct DesktopPrefs {
    pub close_to_tray: bool,
    pub autostart: bool,
    pub start_minimized: bool,
    pub wake_for_schedule: bool,
    pub keep_awake: bool,
    pub notifications: bool,
}

impl Default for DesktopPrefs {
    fn default() -> Self {
        Self {
            close_to_tray: true,
            autostart: false,
            start_minimized: false,
            wake_for_schedule: false,
            keep_awake: true,
            notifications: true,
        }
    }
}

#[derive(Default)]
pub struct Desktop {
    pub prefs: Mutex<DesktopPrefs>,
    pause_item: Mutex<Option<MenuItem<Wry>>>,
    paused: Mutex<bool>,
}

fn prefs_path<R: Runtime>(app: &AppHandle<R>) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("desktop.json"))
}

pub fn load_prefs<R: Runtime>(app: &AppHandle<R>) -> DesktopPrefs {
    prefs_path(app)
        .and_then(|p| fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// `VIDEOSTUDIO_NO_TRAY=1` (used by the installer test) makes closing the window quit.
pub fn close_to_tray(desktop: &Desktop) -> bool {
    std::env::var("VIDEOSTUDIO_NO_TRAY").map_or(true, |v| v.is_empty() || v == "0")
        && desktop.prefs.lock().unwrap().close_to_tray
}

pub fn show_main<R: Runtime>(app: &AppHandle<R>) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

/// Minimal HTTP call to the local backend (no extra HTTP client dependency).
fn backend_request(app: &AppHandle, method: &str, path: &str, body: &str) -> Result<String, String> {
    let backend = app.state::<std::sync::Arc<Backend>>();
    let info = backend.wait_info(Duration::from_secs(5))?;
    let addr = info
        .base_url
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string();
    let mut stream = TcpStream::connect(&addr).map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .map_err(|e| e.to_string())?;
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {addr}\r\nAuthorization: Bearer {}\r\n\
         Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        info.token,
        body.len()
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|e| e.to_string())?;
    let status = response.split_whitespace().nth(1).unwrap_or("0");
    if status.starts_with('2') {
        Ok(response)
    } else {
        Err(format!("backend answered {status}"))
    }
}

fn pause_label(paused: bool) -> &'static str {
    if paused {
        "Tiếp tục hàng đợi"
    } else {
        "Tạm dừng hàng đợi"
    }
}

fn update_pause_item(desktop: &Desktop, paused: bool) {
    *desktop.paused.lock().unwrap() = paused;
    if let Some(item) = desktop.pause_item.lock().unwrap().as_ref() {
        let _ = item.set_text(pause_label(paused));
    }
}

pub fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Mở Video Studio", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", pause_label(false), true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Thoát", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&open, &pause, &separator, &quit])?;
    *app.state::<Desktop>().pause_item.lock().unwrap() = Some(pause);

    let mut builder = TrayIconBuilder::with_id("main")
        .tooltip("Video Studio")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_main(app),
            "pause" => {
                let desktop = app.state::<Desktop>();
                let next = !*desktop.paused.lock().unwrap();
                let body = format!("{{\"paused\":{next}}}");
                match backend_request(app, "PUT", "/queue/state", &body) {
                    Ok(_) => update_pause_item(&desktop, next),
                    Err(err) => eprintln!("tray: could not change queue state: {err}"),
                }
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

fn sync_autostart(app: &AppHandle, enabled: bool) -> Result<(), String> {
    let manager = app.autolaunch();
    let current = manager.is_enabled().unwrap_or(false);
    if enabled && !current {
        manager.enable().map_err(|e| format!("Không bật được khởi động cùng Windows: {e}"))?;
    } else if !enabled && current {
        manager.disable().map_err(|e| format!("Không tắt được khởi động cùng Windows: {e}"))?;
    }
    Ok(())
}

#[tauri::command]
pub fn apply_desktop_prefs(
    app: AppHandle,
    desktop: State<'_, Desktop>,
    prefs: DesktopPrefs,
) -> Result<(), String> {
    if let Some(path) = prefs_path(&app) {
        if let Some(dir) = path.parent() {
            let _ = fs::create_dir_all(dir);
        }
        let _ = fs::write(&path, serde_json::to_string_pretty(&prefs).unwrap_or_default());
    }
    let autostart = prefs.autostart;
    *desktop.prefs.lock().unwrap() = prefs;
    // Development builds would register the debug binary; only touch it in release.
    if cfg!(debug_assertions) {
        return Ok(());
    }
    sync_autostart(&app, autostart)
}

#[tauri::command]
pub fn set_tray_paused(desktop: State<'_, Desktop>, paused: bool) {
    update_pause_item(&desktop, paused);
}

#[tauri::command]
pub fn desktop_notify(
    app: AppHandle,
    desktop: State<'_, Desktop>,
    title: String,
    body: String,
) -> Result<(), String> {
    if !desktop.prefs.lock().unwrap().notifications {
        return Ok(());
    }
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn open_external(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("Chỉ mở được liên kết http(s)".into());
    }
    tauri_plugin_opener::open_url(url, None::<&str>).map_err(|e| e.to_string())
}

/// Open Facebook Login in an app window and return the `login_success.html` URL the
/// dialog redirects to (it carries `code` and `state`). The page itself is never loaded.
#[tauri::command]
pub async fn facebook_login(app: AppHandle, url: String) -> Result<String, String> {
    let parsed: tauri::Url = url.parse().map_err(|_| "URL đăng nhập không hợp lệ".to_string())?;
    if parsed.host_str().map_or(true, |h| !h.ends_with("facebook.com")) {
        return Err("URL đăng nhập phải thuộc facebook.com".into());
    }
    if let Some(old) = app.get_webview_window(FB_LOGIN_LABEL) {
        let _ = old.destroy();
    }
    let (tx, rx) = mpsc::channel::<Result<String, String>>();
    let nav_tx = tx.clone();
    let window = WebviewWindowBuilder::new(&app, FB_LOGIN_LABEL, WebviewUrl::External(parsed))
        .title("Đăng nhập Facebook — Video Studio")
        .inner_size(560.0, 720.0)
        .center()
        .on_navigation(move |nav| {
            if nav.path() == FB_SUCCESS_PATH {
                let _ = nav_tx.send(Ok(nav.to_string()));
                return false;
            }
            true
        })
        .build()
        .map_err(|e| e.to_string())?;
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::Destroyed = event {
            let _ = tx.send(Err("Bạn đã đóng cửa sổ đăng nhập Facebook".into()));
        }
    });
    let result = tauri::async_runtime::spawn_blocking(move || {
        rx.recv_timeout(FB_LOGIN_TIMEOUT)
            .unwrap_or_else(|_| Err("Hết thời gian chờ đăng nhập Facebook".into()))
    })
    .await
    .map_err(|e| e.to_string())?;
    if let Some(w) = app.get_webview_window(FB_LOGIN_LABEL) {
        let _ = w.destroy();
    }
    result
}
