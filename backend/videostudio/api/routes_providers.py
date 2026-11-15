"""Provider types and instances (Settings → "Nhà cung cấp")."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from videostudio.pipeline.tts_step import TTSCache, synthesize_narration
from videostudio.providers.base import (
    CAPABILITIES,
    CAPABILITY_LABELS,
    LLMProvider,
    ResolvedProvider,
    TestResult,
    TTSProvider,
)
from videostudio.providers.errors import ProviderError
from videostudio.providers.service import ProviderNotFound, ProviderValidationError

from .media import media_url
from .services import Services, get_services

router = APIRouter(prefix="/providers", tags=["providers"])


class InstanceCreate(BaseModel):
    type: str
    name: str | None = Field(default=None, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)
    make_default: bool = False


class InstanceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    config: dict[str, Any] | None = None


class TestUnsaved(BaseModel):
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    instance_id: str | None = None


class SampleRequest(BaseModel):
    text: str = Field(default="Xin chào, đây là giọng đọc thử của Video Studio.",
                      max_length=500)
    voice: str | None = None
    lang: str = "vi"


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ProviderNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc.args[0]))
    if isinstance(exc, ProviderValidationError):
        return HTTPException(422, str(exc))
    if isinstance(exc, ProviderError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, exc.to_dict())
    raise exc


@router.get("/types")
def list_types(capability: str | None = None,
               svc: Services = Depends(get_services)) -> dict[str, Any]:
    return {
        "capabilities": [{"id": c, "label": CAPABILITY_LABELS[c]} for c in CAPABILITIES],
        "types": svc.providers.types(capability),
    }


@router.get("")
def list_instances(capability: str | None = None,
                   svc: Services = Depends(get_services)) -> dict[str, Any]:
    return {"instances": svc.providers.list(capability), "defaults": svc.providers.defaults()}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_instance(body: InstanceCreate, svc: Services = Depends(get_services)) -> dict:
    try:
        return svc.providers.create(body.type, body.name, body.config, body.make_default)
    except (ProviderNotFound, ProviderValidationError) as exc:
        raise _errors(exc) from exc


async def _run_test(provider, ctx) -> TestResult:
    try:
        return await provider.test_connection(ctx)
    except ProviderError as exc:
        return TestResult(ok=False, message=exc.message, details={"code": exc.code})


@router.post("/test")
async def test_unsaved(body: TestUnsaved, svc: Services = Depends(get_services)) -> TestResult:
    try:
        provider, ctx = svc.providers.ephemeral_context(body.type, body.config, body.instance_id)
    except (ProviderNotFound, ProviderValidationError) as exc:
        raise _errors(exc) from exc
    return await _run_test(provider, ctx)


@router.get("/{instance_id}")
def get_instance(instance_id: str, svc: Services = Depends(get_services)) -> dict:
    try:
        return svc.providers.get(instance_id)
    except ProviderNotFound as exc:
        raise _errors(exc) from exc


@router.patch("/{instance_id}")
def update_instance(instance_id: str, body: InstanceUpdate,
                    svc: Services = Depends(get_services)) -> dict:
    try:
        return svc.providers.update(instance_id, body.name, body.config)
    except (ProviderNotFound, ProviderValidationError) as exc:
        raise _errors(exc) from exc


@router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instance(instance_id: str, svc: Services = Depends(get_services)) -> None:
    try:
        svc.providers.delete(instance_id)
    except ProviderNotFound as exc:
        raise _errors(exc) from exc


@router.post("/{instance_id}/default")
def set_default(instance_id: str, svc: Services = Depends(get_services)) -> dict:
    try:
        return svc.providers.set_default(instance_id)
    except ProviderNotFound as exc:
        raise _errors(exc) from exc


@router.post("/{instance_id}/test")
async def test_instance(instance_id: str, svc: Services = Depends(get_services)) -> TestResult:
    try:
        provider, ctx, _ = svc.providers.context(instance_id)
    except ProviderNotFound as exc:
        raise _errors(exc) from exc
    return await _run_test(provider, ctx)


@router.get("/{instance_id}/models")
async def list_models(instance_id: str, svc: Services = Depends(get_services)) -> dict:
    try:
        provider, ctx, _ = svc.providers.context(instance_id)
        if not isinstance(provider, LLMProvider):
            raise ProviderValidationError("Nhà cung cấp này không có danh sách model")
        models = await provider.list_models(ctx)
    except (ProviderNotFound, ProviderValidationError, ProviderError) as exc:
        raise _errors(exc) from exc
    return {"models": [m.model_dump() for m in models]}


@router.get("/{instance_id}/voices")
async def list_voices(instance_id: str, lang: str | None = "vi",
                      svc: Services = Depends(get_services)) -> dict:
    try:
        provider, ctx, _ = svc.providers.context(instance_id)
        if not isinstance(provider, TTSProvider):
            raise ProviderValidationError("Nhà cung cấp này không có danh sách giọng")
        voices = await provider.list_voices(ctx, lang or None)
    except (ProviderNotFound, ProviderValidationError, ProviderError) as exc:
        raise _errors(exc) from exc
    return {"voices": [v.model_dump() for v in voices],
            "default_voice": provider.default_voice(ctx)}


@router.post("/{instance_id}/sample")
async def voice_sample(instance_id: str, body: SampleRequest,
                       svc: Services = Depends(get_services)) -> dict:
    """Synthesise a short sample (cached) and return a URL the <audio> element can play."""
    try:
        provider, ctx, name = svc.providers.context(instance_id)
        if not isinstance(provider, TTSProvider):
            raise ProviderValidationError("Không phải nhà cung cấp giọng đọc")
        resolved = ResolvedProvider(provider=provider, ctx=ctx, instance_id=instance_id,
                                    instance_name=name, voice=body.voice or None)
        cache = TTSCache(svc.settings.data_dir / "cache" / "tts")
        result = await synthesize_narration(resolved, body.text, body.lang, cache)
    except (ProviderNotFound, ProviderValidationError, ProviderError) as exc:
        raise _errors(exc) from exc
    rel = result.audio_path.resolve().relative_to(svc.settings.data_dir.resolve()).as_posix()
    digest = hashlib.sha256(rel.encode()).hexdigest()[:8]
    return {"url": media_url(svc, rel), "duration_s": result.duration_s, "cached": result.cached,
            "words": len(result.words), "id": digest}
