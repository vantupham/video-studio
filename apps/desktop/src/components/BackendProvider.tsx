import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiClient } from "../lib/api";
import { resolveBackend } from "../lib/backend";
import { BackendContext, type BackendStatus } from "../lib/backendContext";
import type { AppEvent, AppNotification, Job, JobEvent, JobLogEntry } from "../lib/types";
import { notifyDesktop } from "../lib/desktop";

const MAX_HEALTH_ATTEMPTS = 120; // ~60 s: first launch of a PyInstaller app can be slow on Windows
const MAX_LOG_LINES = 300;
const MAX_NOTIFICATIONS = 100;
const TOAST_MS = 6000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function BackendProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<BackendStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [attempts, setAttempts] = useState(0);
  const [api, setApi] = useState<ApiClient | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [logs, setLogs] = useState<Record<string, JobLogEntry[]>>({});
  const [generation, setGeneration] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const listeners = useRef(new Set<(event: AppEvent) => void>());
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [toasts, setToasts] = useState<AppNotification[]>([]);
  const [queuePaused, setQueuePausedState] = useState(false);

  const subscribe = useCallback((listener: (event: AppEvent) => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const pushNotification = useCallback(
    (event: AppNotification & { type?: string }) => {
      const n: AppNotification = { ...event };
      delete (n as { type?: string }).type;
      setNotifications((prev) =>
        [n, ...prev.filter((p) => p.id !== n.id)].slice(0, MAX_NOTIFICATIONS),
      );
      setToasts((prev) => [...prev.filter((p) => p.id !== n.id), n].slice(-4));
      window.setTimeout(() => dismissToast(n.id), TOAST_MS);
      if (n.level !== "info") void notifyDesktop(n.title, n.message);
    },
    [dismissToast],
  );

  const markNotificationsRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    api?.markNotificationsRead().catch(() => undefined);
  }, [api]);

  const setQueuePaused = useCallback(
    async (paused: boolean) => {
      if (!api) return;
      const res = await api.setQueuePaused(paused);
      setQueuePausedState(res.paused);
    },
    [api],
  );

  const upsertJob = useCallback((job: Job) => {
    setJobs((prev) => ({ ...prev, [job.id]: { ...prev[job.id], ...job } }));
    if (job.logs) {
      setLogs((prev) => ({ ...prev, [job.id]: job.logs!.slice(-MAX_LOG_LINES) }));
    }
  }, []);

  const retry = useCallback(() => {
    setStatus("connecting");
    setError(null);
    setAttempts(0);
    setGeneration((g) => g + 1);
  }, []);

  // Resolve the backend and wait until /health answers.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      let client: ApiClient;
      try {
        client = new ApiClient(await resolveBackend());
      } catch (e) {
        if (!cancelled) {
          setStatus("error");
          setError(String(e));
        }
        return;
      }
      let lastError = "";
      for (let i = 1; i <= MAX_HEALTH_ATTEMPTS && !cancelled; i++) {
        setAttempts(i);
        try {
          await client.health();
          if (!cancelled) {
            setApi(client);
            setStatus("ready");
          }
          return;
        } catch (e) {
          lastError = e instanceof Error ? e.message : String(e);
          // Wrong token will not fix itself.
          if (lastError.includes("token")) break;
        }
        await sleep(500);
      }
      if (!cancelled) {
        setStatus("error");
        setError(lastError || "Không kết nối được backend");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [generation]);

  // Single WebSocket for job events, reconnecting automatically.
  useEffect(() => {
    if (!api) return;
    let stopped = false;
    let retryTimer: number | undefined;

    const connect = () => {
      const ws = new WebSocket(api.wsUrl("/ws/jobs"));
      wsRef.current = ws;
      ws.onopen = () => {
        setWsConnected(true);
        // Catch up on anything missed while disconnected.
        api
          .notifications()
          .then((items) => setNotifications(items.slice(0, MAX_NOTIFICATIONS)))
          .catch(() => undefined);
        api
          .queueState()
          .then((q) => setQueuePausedState(q.paused))
          .catch(() => undefined);
      };
      ws.onmessage = (msg) => {
        const event = JSON.parse(msg.data) as JobEvent | AppEvent;
        if (event.type === "notification") {
          pushNotification(event);
        } else if (event.type === "queue") {
          setQueuePausedState(event.paused);
        }
        if (event.type !== "job" && event.type !== "job_log" && event.type !== "hello" &&
          event.type !== "ping") {
          for (const l of listeners.current) l(event);
          return;
        }
        if (event.type === "job") {
          setJobs((prev) => ({ ...prev, [event.job.id]: { ...prev[event.job.id], ...event.job } }));
        } else if (event.type === "job_log") {
          setLogs((prev) => {
            const existing = prev[event.job_id] ?? [];
            const entry = { ts: event.ts, level: event.level, message: event.message };
            return { ...prev, [event.job_id]: [...existing, entry].slice(-MAX_LOG_LINES) };
          });
        }
      };
      ws.onclose = () => {
        setWsConnected(false);
        if (!stopped) retryTimer = window.setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [api, pushNotification]);

  return (
    <BackendContext.Provider
      value={{
        status,
        error,
        attempts,
        api,
        wsConnected,
        jobs,
        logs,
        upsertJob,
        retry,
        subscribe,
        notifications,
        markNotificationsRead,
        toasts,
        dismissToast,
        queuePaused,
        setQueuePaused,
      }}
    >
      {children}
    </BackendContext.Provider>
  );
}
