from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from videostudio.jobs import JOB_STATES, InvalidJobState

from .auth import websocket_token_ok
from .schemas import DemoJobRequest
from .services import Services, get_services, get_ws_services

router = APIRouter(prefix="/jobs", tags=["jobs"])
ws_router = APIRouter()


@router.get("")
def list_jobs(
    project_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 100,
    svc: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    if status_filter and status_filter not in JOB_STATES:
        raise HTTPException(422, f"status must be one of {JOB_STATES}")
    return svc.queue.list_jobs(project_id=project_id, status=status_filter,
                               limit=max(1, min(limit, 500)))


@router.post("/demo", status_code=status.HTTP_202_ACCEPTED)
def create_demo_job(
    body: DemoJobRequest | None = None, svc: Services = Depends(get_services)
) -> dict[str, Any]:
    return svc.queue.enqueue("demo", (body or DemoJobRequest()).model_dump())


@router.get("/{job_id}")
def get_job(job_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    job = svc.queue.get(job_id, include_logs=True)
    if job is None:
        raise HTTPException(404, "Không tìm thấy job")
    return job


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.queue.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(404, "Không tìm thấy job") from exc
    except InvalidJobState as exc:
        raise HTTPException(409, str(exc)) from exc


@ws_router.websocket("/ws/jobs")
async def jobs_ws(websocket: WebSocket) -> None:
    """Streams ``{"type": "job", "job": {...}}`` and ``{"type": "job_log", ...}`` events."""
    if not websocket_token_ok(websocket):
        await websocket.close(code=4401, reason="invalid token")
        return
    svc = get_ws_services(websocket)
    await websocket.accept()
    queue = svc.bus.subscribe()
    project_filter = websocket.query_params.get("project_id")

    async def drain_client() -> None:
        # Detect disconnects; clients do not need to send anything.
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                await websocket.receive_text()

    reader = asyncio.create_task(drain_client())
    try:
        await websocket.send_json({"type": "hello"})
        while not reader.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            if project_filter and event.get("type") == "job":
                if event["job"].get("project_id") != project_filter:
                    continue
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        reader.cancel()
        svc.bus.unsubscribe(queue)
