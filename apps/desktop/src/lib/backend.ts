export type BackendMode = "tauri" | "browser";

export interface BackendInfo {
  baseUrl: string;
  token: string;
  mode: BackendMode;
}

interface TauriBackendInfo {
  base_url: string;
  token: string;
}

export const DEFAULT_BROWSER_BACKEND = "http://127.0.0.1:8765";
export const DEFAULT_DEV_TOKEN = "dev-token";

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * In the desktop app the Rust shell owns the sidecar and hands us its URL and the
 * per-launch token. In a plain browser (dev/testing) we use ?backend=&token= query
 * params, then VITE_BACKEND_URL / VITE_BACKEND_TOKEN, then the dev defaults.
 */
export async function resolveBackend(): Promise<BackendInfo> {
  if (isTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    const info = await invoke<TauriBackendInfo>("backend_info");
    return { baseUrl: info.base_url.replace(/\/$/, ""), token: info.token, mode: "tauri" };
  }
  const params = new URLSearchParams(window.location.search);
  const baseUrl =
    params.get("backend") || import.meta.env.VITE_BACKEND_URL || DEFAULT_BROWSER_BACKEND;
  const token = params.get("token") || import.meta.env.VITE_BACKEND_TOKEN || DEFAULT_DEV_TOKEN;
  return { baseUrl: baseUrl.replace(/\/$/, ""), token, mode: "browser" };
}
