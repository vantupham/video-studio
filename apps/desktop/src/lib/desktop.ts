import { isTauri } from "./backend";
import type { DesktopPrefs } from "./types";

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const core = await import("@tauri-apps/api/core");
  return core.invoke<T>(cmd, args);
}

/** Windows toast / OS notification (desktop app only; in-app toasts cover the browser). */
export async function notifyDesktop(title: string, body: string): Promise<void> {
  if (!isTauri()) return;
  try {
    await invoke("desktop_notify", { title, body });
  } catch {
    // Notifications are best effort.
  }
}

/** Push tray / autostart / close-to-tray preferences to the Rust shell. */
export async function applyDesktopPrefs(prefs: DesktopPrefs): Promise<void> {
  if (!isTauri()) return;
  await invoke("apply_desktop_prefs", { prefs });
}

export async function setTrayPaused(paused: boolean): Promise<void> {
  if (!isTauri()) return;
  try {
    await invoke("set_tray_paused", { paused });
  } catch {
    // Older shells without a tray.
  }
}

/**
 * Open Facebook Login in an app window and resolve with the final
 * ``login_success.html#access_token=…`` / ``?code=…`` URL (desktop app only).
 */
export async function facebookLogin(authUrl: string): Promise<string> {
  return invoke<string>("facebook_login", { url: authUrl });
}

export async function openExternal(url: string): Promise<void> {
  if (isTauri()) {
    try {
      await invoke("open_external", { url });
      return;
    } catch {
      // fall through to window.open
    }
  }
  window.open(url, "_blank", "noopener");
}

export { isTauri };
