"""Persistent job queue: a SQLite table plus in-process worker threads.

Each worker thread owns an asyncio event loop so job handlers (and plugins) can be
``async``. Jobs survive restarts: anything left ``running`` when the process died
is put back to ``queued`` by :meth:`JobQueue.requeue_stale` at startup.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from videostudio.db import Job, JobLog, utcnow
from videostudio.plugins.base import JobCancelled, JobContext

from .events import EventBus

log = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
JOB_STATES = (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED)
FINAL_STATES = (SUCCEEDED, FAILED, CANCELLED)


class UnknownJobType(Exception):
    pass


class InvalidJobState(Exception):
    pass


@dataclass(frozen=True)
class JobSnapshot:
    id: str
    type: str
    project_id: str | None
    payload: dict[str, Any]
    attempts: int


Handler = Callable[[JobSnapshot, JobContext], Awaitable[dict[str, Any] | None]]


def job_to_dict(job: Job, include_logs: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": job.id,
        "type": job.type,
        "project_id": job.project_id,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "payload": job.payload,
        "result": job.result,
        "error": job.error,
        "attempts": job.attempts,
        "cancel_requested": job.cancel_requested,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    if include_logs:
        data["logs"] = [
            {"ts": e.ts.isoformat(), "level": e.level, "message": e.message} for e in job.logs
        ]
    return data


class JobQueue:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        bus: EventBus | None = None,
        workers: int = 2,
        poll_interval: float = 1.0,
    ) -> None:
        self._sf = session_factory
        self.bus = bus or EventBus()
        self._handlers: dict[str, Handler] = {}
        self._project_dir_resolver: Callable[[str], Any] | None = None
        self._n_workers = workers
        self._poll_interval = poll_interval
        self._threads: list[threading.Thread] = []
        self._wakeup = threading.Event()
        self._stopping = threading.Event()
        self._claim_lock = threading.Lock()
        self._cancel_requests: set[str] = set()
        self._cancel_lock = threading.Lock()

    # ------------------------------------------------------------------ registry
    def register(self, job_type: str, handler: Handler) -> None:
        self._handlers[job_type] = handler

    @property
    def job_types(self) -> list[str]:
        return sorted(self._handlers)

    def set_project_dir_resolver(self, resolver: Callable[[str], Any]) -> None:
        self._project_dir_resolver = resolver

    # ------------------------------------------------------------------ public API
    def enqueue(
        self, job_type: str, payload: dict[str, Any] | None = None, project_id: str | None = None
    ) -> dict[str, Any]:
        if job_type not in self._handlers:
            raise UnknownJobType(job_type)
        with self._sf.begin() as s:
            job = Job(type=job_type, payload=payload or {}, project_id=project_id, status=QUEUED,
                      message="Đang chờ")
            s.add(job)
            s.flush()
            s.add(JobLog(job_id=job.id, message=f"Enqueued job '{job_type}'"))
            data = job_to_dict(job)
        self._publish_job(data)
        self._wakeup.set()
        return data

    def get(self, job_id: str, include_logs: bool = True) -> dict[str, Any] | None:
        with self._sf() as s:
            job = s.get(Job, job_id)
            return job_to_dict(job, include_logs=include_logs) if job else None

    def list_jobs(
        self, project_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._sf() as s:
            stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
            if project_id:
                stmt = stmt.where(Job.project_id == project_id)
            if status:
                stmt = stmt.where(Job.status == status)
            return [job_to_dict(j) for j in s.scalars(stmt)]

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._sf.begin() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in FINAL_STATES:
                raise InvalidJobState(f"Job is already {job.status}")
            if job.status == QUEUED:
                job.status = CANCELLED
                job.finished_at = utcnow()
                job.message = "Đã huỷ"
                s.add(JobLog(job_id=job.id, level="warning", message="Cancelled while queued"))
            else:
                job.cancel_requested = True
                job.message = "Đang huỷ…"
                s.add(JobLog(job_id=job.id, level="warning", message="Cancellation requested"))
                with self._cancel_lock:
                    self._cancel_requests.add(job_id)
            data = job_to_dict(job)
        self._publish_job(data)
        return data

    def requeue_stale(self) -> int:
        """Reset jobs left ``running`` by a crashed/killed process."""
        count = 0
        with self._sf.begin() as s:
            for job in s.scalars(select(Job).where(Job.status == RUNNING)):
                if job.cancel_requested:
                    job.status = CANCELLED
                    job.finished_at = utcnow()
                    job.message = "Đã huỷ"
                    msg = "Marked cancelled at startup (cancel was pending)"
                else:
                    job.status = QUEUED
                    job.progress = 0
                    job.started_at = None
                    job.message = "Đang chờ (khởi động lại)"
                    msg = "Re-queued at startup after unexpected shutdown"
                    count += 1
                s.add(JobLog(job_id=job.id, level="warning", message=msg))
        if count:
            log.warning("Re-queued %d stale running job(s)", count)
        return count

    # ------------------------------------------------------------------ workers
    def start(self) -> None:
        if self._threads:
            return
        self._stopping.clear()
        for n in range(self._n_workers):
            t = threading.Thread(target=self._worker_main, name=f"job-worker-{n}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 10.0) -> None:
        self._stopping.set()
        self._wakeup.set()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
        self._threads.clear()

    def run_next_sync(self) -> bool:
        """Claim and run one job in the calling thread (used by tests)."""
        snapshot = self._claim()
        if snapshot is None:
            return False
        asyncio.run(self._execute(snapshot))
        return True

    def _worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self._stopping.is_set():
                snapshot = self._claim()
                if snapshot is None:
                    self._wakeup.wait(self._poll_interval)
                    self._wakeup.clear()
                    continue
                loop.run_until_complete(self._execute(snapshot))
        finally:
            loop.close()

    def _claim(self) -> JobSnapshot | None:
        with self._claim_lock, self._sf.begin() as s:
            job = s.scalars(
                select(Job).where(Job.status == QUEUED).order_by(Job.created_at).limit(1)
            ).first()
            if job is None:
                return None
            res = s.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == QUEUED)
                .values(status=RUNNING, started_at=utcnow(), attempts=Job.attempts + 1,
                        message="Đang chạy", error=None)
            )
            if res.rowcount != 1:
                return None
            s.add(JobLog(job_id=job.id, message="Started"))
            s.flush()
            s.refresh(job)
            snapshot = JobSnapshot(job.id, job.type, job.project_id, dict(job.payload or {}),
                                   job.attempts)
            data = job_to_dict(job)
        self._publish_job(data)
        return snapshot

    async def _execute(self, snap: JobSnapshot) -> None:
        handler = self._handlers.get(snap.type)
        project_dir = None
        if snap.project_id and self._project_dir_resolver:
            project_dir = self._project_dir_resolver(snap.project_id)
        ctx = JobContext(
            job_id=snap.id,
            project_dir=project_dir,
            config=snap.payload.get("config", {}) if isinstance(snap.payload, dict) else {},
            progress_cb=lambda pct, msg: self._set_progress(snap.id, pct, msg),
            log_cb=lambda msg, level: self._log(snap.id, msg, level),
            cancelled_cb=lambda: self._should_stop(snap.id),
        )
        try:
            if handler is None:
                raise UnknownJobType(snap.type)
            result = await handler(snap, ctx)
        except JobCancelled:
            if self._stopping.is_set() and not self._is_cancel_requested(snap.id):
                self._finish(snap.id, QUEUED, message="Đang chờ (bị gián đoạn)",
                             log_msg="Interrupted by shutdown; re-queued")
            else:
                self._finish(snap.id, CANCELLED, message="Đã huỷ", log_msg="Cancelled")
        except Exception as exc:  # noqa: BLE001
            user_message = getattr(exc, "user_message", None)
            if user_message is None and type(exc).__module__.startswith("videostudio.providers"):
                user_message = str(exc)
            if user_message is not None:
                # expected failures (bad key, quota, timeout…): no traceback noise
                log.warning("Job %s failed: %s", snap.id, user_message)
                self._log(snap.id, user_message, "error")
                error = user_message
            else:
                log.exception("Job %s failed", snap.id)
                self._log(snap.id, traceback.format_exc(), "error")
                error = f"{type(exc).__name__}: {exc}"
            self._finish(snap.id, FAILED, message="Lỗi", error=error, log_msg="Failed")
        else:
            self._finish(snap.id, SUCCEEDED, message="Hoàn tất", result=result or {},
                         progress=100, log_msg="Succeeded")
        finally:
            with self._cancel_lock:
                self._cancel_requests.discard(snap.id)

    # ------------------------------------------------------------------ helpers
    def _is_cancel_requested(self, job_id: str) -> bool:
        with self._cancel_lock:
            return job_id in self._cancel_requests

    def _should_stop(self, job_id: str) -> bool:
        return self._stopping.is_set() or self._is_cancel_requested(job_id)

    def _set_progress(self, job_id: str, pct: int, message: str | None) -> None:
        with self._sf.begin() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.progress = pct
            if message:
                job.message = message
            data = job_to_dict(job)
        self._publish_job(data)

    def _log(self, job_id: str, message: str, level: str = "info") -> None:
        with self._sf.begin() as s:
            entry = JobLog(job_id=job_id, level=level, message=message)
            s.add(entry)
            s.flush()
            ts = entry.ts.isoformat()
        self.bus.publish(
            {"type": "job_log", "job_id": job_id, "level": level, "message": message, "ts": ts}
        )

    def _finish(
        self,
        job_id: str,
        status: str,
        *,
        message: str,
        log_msg: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        progress: int | None = None,
    ) -> None:
        with self._sf.begin() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = status
            job.message = message
            job.error = error
            if result is not None:
                job.result = result
            if progress is not None:
                job.progress = progress
            if status == QUEUED:
                job.started_at = None
                job.progress = 0
            else:
                job.finished_at = utcnow()
            s.add(JobLog(job_id=job_id, level="error" if error else "info", message=log_msg))
            data = job_to_dict(job)
        self._publish_job(data)

    def _publish_job(self, data: dict[str, Any]) -> None:
        self.bus.publish({"type": "job", "job": data})
