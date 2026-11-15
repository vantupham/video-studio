import { createContext, useContext, useEffect } from "react";
import type { ApiClient } from "./api";
import type { Job, JobLogEntry } from "./types";

export type BackendStatus = "connecting" | "ready" | "error";

export interface BackendContextValue {
  status: BackendStatus;
  error: string | null;
  attempts: number;
  api: ApiClient | null;
  wsConnected: boolean;
  jobs: Record<string, Job>;
  logs: Record<string, JobLogEntry[]>;
  upsertJob: (job: Job) => void;
  retry: () => void;
}

export const BackendContext = createContext<BackendContextValue | null>(null);

export function useBackend(): BackendContextValue {
  const ctx = useContext(BackendContext);
  if (!ctx) throw new Error("useBackend must be used inside <BackendProvider>");
  return ctx;
}

export function useApi(): ApiClient {
  const { api } = useBackend();
  if (!api) throw new Error("Backend is not ready");
  return api;
}

export const FINAL_JOB_STATES = new Set(["succeeded", "failed", "cancelled"]);

/** Live view of one job: WebSocket events, with polling as a fallback. */
export function useJob(jobId: string | null | undefined): {
  job: Job | null;
  logs: JobLogEntry[];
} {
  const { api, jobs, logs, upsertJob, wsConnected } = useBackend();
  const job = jobId ? (jobs[jobId] ?? null) : null;
  const status = job?.status;

  useEffect(() => {
    if (!api || !jobId) return;
    let cancelled = false;
    api
      .getJob(jobId)
      .then((j) => !cancelled && upsertJob(j))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [api, jobId, upsertJob]);

  useEffect(() => {
    if (!api || !jobId || wsConnected || (status && FINAL_JOB_STATES.has(status))) return;
    const timer = window.setInterval(() => {
      api
        .getJob(jobId)
        .then(upsertJob)
        .catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [api, jobId, wsConnected, status, upsertJob]);

  return { job, logs: (jobId && logs[jobId]) || job?.logs || [] };
}
