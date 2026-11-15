from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from videostudio.db import Job, JobLog
from videostudio.jobs import (
    CANCELLED,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    EventBus,
    InvalidJobState,
    JobQueue,
    UnknownJobType,
)
from videostudio.jobs.handlers import demo_job


def make_queue(session_factory, workers: int = 1, bus: EventBus | None = None) -> JobQueue:
    q = JobQueue(session_factory, bus=bus, workers=workers, poll_interval=0.05)
    q.register("demo", demo_job)
    return q


def wait_for(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_enqueue_and_run_sync(session_factory) -> None:
    q = make_queue(session_factory)
    job = q.enqueue("demo", {"seconds": 0, "steps": 3})
    assert job["status"] == QUEUED
    assert q.run_next_sync() is True
    done = q.get(job["id"])
    assert done["status"] == SUCCEEDED
    assert done["progress"] == 100
    assert done["result"] == {"steps": 3, "seconds": 0.0}
    assert done["attempts"] == 1
    messages = [entry["message"] for entry in done["logs"]]
    assert "Step 3/3 done" in messages and messages[-1] == "Succeeded"
    assert q.run_next_sync() is False


def test_failure_is_recorded(session_factory) -> None:
    q = make_queue(session_factory)
    job = q.enqueue("demo", {"seconds": 0, "steps": 5, "fail_at_step": 2})
    q.run_next_sync()
    done = q.get(job["id"])
    assert done["status"] == FAILED
    assert "Demo failure at step 2" in done["error"]
    assert done["progress"] == 20
    assert any(e["level"] == "error" for e in done["logs"])


def test_unknown_job_type(session_factory) -> None:
    with pytest.raises(UnknownJobType):
        make_queue(session_factory).enqueue("nope")


def test_cancel_queued(session_factory) -> None:
    q = make_queue(session_factory)
    job = q.enqueue("demo", {"seconds": 0})
    assert q.cancel(job["id"])["status"] == CANCELLED
    assert q.run_next_sync() is False
    with pytest.raises(InvalidJobState):
        q.cancel(job["id"])


def test_cancel_running_with_workers(session_factory) -> None:
    q = make_queue(session_factory)
    q.start()
    try:
        job = q.enqueue("demo", {"seconds": 20, "steps": 200})
        wait_for(lambda: q.get(job["id"])["status"] == RUNNING)
        res = q.cancel(job["id"])
        assert res["cancel_requested"] is True
        final = wait_for(lambda: (j := q.get(job["id"]))["status"] == CANCELLED and j)
        assert final["finished_at"] is not None
    finally:
        q.stop()


def test_workers_process_in_order_and_publish_events(session_factory) -> None:
    bus = EventBus()
    events: list[dict] = []

    async def collect() -> None:
        sub = bus.subscribe()
        q = make_queue(session_factory, workers=2, bus=bus)
        q.start()
        try:
            ids = [q.enqueue("demo", {"seconds": 0.05, "steps": 2})["id"] for _ in range(3)]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    events.append(await asyncio.wait_for(sub.get(), 0.5))
                except TimeoutError:
                    pass
                if all(q.get(i, include_logs=False)["status"] == SUCCEEDED for i in ids):
                    break
            # let pending call_soon_threadsafe callbacks run
            await asyncio.sleep(0.1)
            while not sub.empty():
                events.append(sub.get_nowait())
        finally:
            q.stop()

    asyncio.run(collect())
    statuses = [e["job"]["status"] for e in events if e["type"] == "job"]
    assert statuses.count(SUCCEEDED) == 3
    assert any(e["type"] == "job_log" for e in events)
    progresses = [e["job"]["progress"] for e in events if e["type"] == "job"]
    assert 50 in progresses


def test_requeue_stale_running_jobs(session_factory) -> None:
    """Simulate the app dying while a job was running, then restarting."""
    q1 = make_queue(session_factory)
    job = q1.enqueue("demo", {"seconds": 0, "steps": 2})
    pending_cancel = q1.enqueue("demo", {"seconds": 0})
    # Mark both as running, as a crashed process would have left them.
    with session_factory.begin() as s:
        for jid in (job["id"], pending_cancel["id"]):
            row = s.get(Job, jid)
            row.status = RUNNING
            row.progress = 40
            row.attempts = 1
        s.get(Job, pending_cancel["id"]).cancel_requested = True

    q2 = make_queue(session_factory)  # "new process"
    assert q2.requeue_stale() == 1
    requeued = q2.get(job["id"])
    assert requeued["status"] == QUEUED
    assert requeued["progress"] == 0
    assert q2.get(pending_cancel["id"])["status"] == CANCELLED
    with session_factory() as s:
        logs = s.scalars(select(JobLog.message).where(JobLog.job_id == job["id"])).all()
    assert any("Re-queued" in m for m in logs)

    assert q2.run_next_sync() is True
    done = q2.get(job["id"])
    assert done["status"] == SUCCEEDED
    assert done["attempts"] == 2


def test_shutdown_requeues_running_job(session_factory) -> None:
    q = make_queue(session_factory)
    q.start()
    job = q.enqueue("demo", {"seconds": 30, "steps": 300})
    wait_for(lambda: q.get(job["id"])["status"] == RUNNING)
    q.stop()
    assert q.get(job["id"])["status"] == QUEUED
