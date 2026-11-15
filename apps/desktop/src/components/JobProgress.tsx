import { useEffect, useRef } from "react";
import { FINAL_JOB_STATES, useApi, useJob } from "../lib/backendContext";
import { JOB_TYPE_LABELS } from "../lib/labels";
import type { Job, JobStep } from "../lib/types";
import { Button, StatusBadge } from "./ui";

type StepState = "done" | "active" | "pending" | "failed";

function stepStates(job: Job, steps: JobStep[]): StepState[] {
  return steps.map((s) => {
    if (job.status === "succeeded") return "done";
    if (job.progress >= s.end && s.end < 100) return "done";
    if (job.progress >= s.start) {
      if (job.status === "failed" || job.status === "cancelled") return "failed";
      return "active";
    }
    return "pending";
  });
}

const STEP_STYLES: Record<StepState, string> = {
  done: "border-emerald-300 bg-emerald-50 text-emerald-800",
  active: "border-indigo-400 bg-indigo-50 text-indigo-800 animate-pulse",
  pending: "border-slate-200 bg-white text-slate-400",
  failed: "border-rose-300 bg-rose-50 text-rose-800",
};

const STEP_ICONS: Record<StepState, string> = {
  done: "✓",
  active: "●",
  pending: "○",
  failed: "✕",
};

export function JobSteps({ job }: { job: Job }) {
  const steps = Array.isArray(job.payload?.steps) ? (job.payload.steps as JobStep[]) : [];
  if (steps.length === 0) return null;
  const states = stepStates(job, steps);
  return (
    <ol className="flex flex-wrap gap-2" data-testid="job-steps">
      {steps.map((s, i) => (
        <li
          key={s.id}
          data-state={states[i]}
          className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium ${STEP_STYLES[states[i]]}`}
        >
          <span aria-hidden>{STEP_ICONS[states[i]]}</span>
          {s.label}
        </li>
      ))}
    </ol>
  );
}

export function JobProgress({ jobId, onFinished }: { jobId: string; onFinished?: () => void }) {
  const api = useApi();
  const { job, logs } = useJob(jobId);
  const finishedRef = useRef(false);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (job && FINAL_JOB_STATES.has(job.status) && !finishedRef.current) {
      finishedRef.current = true;
      onFinished?.();
    }
  }, [job, onFinished]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [logs.length]);

  if (!job) return <p className="text-sm text-slate-500">Đang tải job…</p>;
  const active = !FINAL_JOB_STATES.has(job.status);
  const barColor =
    job.status === "failed"
      ? "bg-rose-500"
      : job.status === "cancelled"
        ? "bg-amber-500"
        : job.status === "succeeded"
          ? "bg-emerald-500"
          : "bg-indigo-500";

  return (
    <div className="space-y-3" data-testid="job-progress">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-medium text-slate-900">
            {JOB_TYPE_LABELS[job.type] ?? job.type}
          </span>
          <StatusBadge status={job.status} />
        </div>
        {active && (
          <Button
            variant="secondary"
            disabled={job.cancel_requested}
            onClick={() => api.cancelJob(job.id).catch(() => undefined)}
          >
            Huỷ
          </Button>
        )}
      </div>
      <JobSteps job={job} />
      <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full transition-all duration-300 ${barColor}`}
          style={{ width: `${job.progress}%` }}
          role="progressbar"
          aria-valuenow={job.progress}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
      <div className="flex justify-between text-xs text-slate-600">
        <span>{job.message}</span>
        <span className="tabular-nums">{job.progress}%</span>
      </div>
      {job.error && <p className="text-sm text-rose-700">{job.error}</p>}
      {logs.length > 0 && (
        <pre
          ref={logRef}
          className="max-h-40 overflow-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100"
        >
          {logs
            .map((l) => `${new Date(l.ts).toLocaleTimeString("vi-VN")}  ${l.message}`)
            .join("\n")}
        </pre>
      )}
    </div>
  );
}
