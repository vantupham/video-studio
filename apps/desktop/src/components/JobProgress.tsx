import { useEffect, useRef } from "react";
import { FINAL_JOB_STATES, useApi, useJob } from "../lib/backendContext";
import { Button, StatusBadge } from "./ui";

const JOB_TYPE_LABELS: Record<string, string> = {
  ingest: "Phân tích nội dung (ingest)",
  demo: "Job demo",
};

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
