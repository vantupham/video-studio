from __future__ import annotations

import platform
import sys
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool

from videostudio import __version__
from videostudio.ir import load_json_schema
from videostudio.media import detect_ffmpeg
from videostudio.secretstore import SecretStoreError

from .schemas import SecretTestRequest
from .services import Services, get_services

router = APIRouter(tags=["system"])

SELFTEST_SECRET = "videostudio.selftest"


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "time": datetime.now(UTC).isoformat()}


@router.get("/system/info")
async def system_info(svc: Services = Depends(get_services)) -> dict[str, Any]:
    ffmpeg = await run_in_threadpool(detect_ffmpeg, [svc.settings.data_dir / "bin"])
    return {
        "version": __version__,
        "dev": svc.settings.dev,
        "data_dir": str(svc.settings.data_dir),
        "db_path": str(svc.settings.db_path),
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        "ffmpeg": ffmpeg.to_dict(),
        "secret_store": svc.secrets.backend_name,
        "workers": svc.settings.workers,
        "job_types": svc.queue.job_types,
        "plugins": {
            "input_adapters": sorted(svc.registry.input_adapters),
            "llm_providers": sorted(svc.registry.llm_providers),
            "tts_providers": sorted(svc.registry.tts_providers),
            "renderers": sorted(svc.registry.renderers),
            "publishers": sorted(svc.registry.publishers),
            "errors": [e.__dict__ for e in svc.registry.errors],
        },
    }


@router.get("/adapters")
def list_adapters(svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "file_extensions": list(a.file_extensions),
            "config_schema": a.config_schema(),
            "origin": svc.registry.origins.get(f"input_adapter:{a.id}", "builtin"),
        }
        for a in svc.registry.input_adapters.values()
    ]


@router.get("/ir/schema")
def ir_schema() -> dict[str, Any]:
    schema = load_json_schema()
    if schema is None:
        raise HTTPException(404, "content-ir.schema.json not bundled")
    return schema


@router.get("/secrets/selftest")
def get_selftest_secret(svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        value = svc.secrets.get(SELFTEST_SECRET)
    except SecretStoreError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"backend": svc.secrets.backend_name, "value": value}


@router.post("/secrets/selftest")
def selftest_secret(body: SecretTestRequest, svc: Services = Depends(get_services)) -> dict:
    try:
        svc.secrets.set(SELFTEST_SECRET, body.value)
        read_back = svc.secrets.get(SELFTEST_SECRET)
    except SecretStoreError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {
        "backend": svc.secrets.backend_name,
        "ok": read_back == body.value,
        "read_back": read_back,
    }
