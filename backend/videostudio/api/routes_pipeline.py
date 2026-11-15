"""Video pipeline API: styles, generation settings, storyboards, renders, assets,
branding."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from videostudio.db import Asset, Project, Publication, Render, utcnow
from videostudio.db import Storyboard as StoryboardRow
from videostudio.media.ffmpeg import CREATE_NO_WINDOW
from videostudio.pipeline.models import GenerationSettings, storyboard_json_schema
from videostudio.pipeline.service import RENDER_STEPS, SCRIPT_STEPS
from videostudio.review import lifecycle
from videostudio.textutil import safe_filename

from .media import media_url
from .services import Services, get_services

router = APIRouter(tags=["pipeline"])

MAX_ASSET_BYTES = 200 * 1024 * 1024
ASSET_KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"},
    "logo": {".png", ".jpg", ".jpeg", ".webp"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"},
}


def _validation(exc: ValidationError) -> HTTPException:
    return HTTPException(422,
                         [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()])


def _project_or_404(svc: Services, project_id: str) -> None:
    with svc.session_factory() as s:
        if s.get(Project, project_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy dự án")


# ----------------------------------------------------------------------------- styles


@router.get("/styles")
def list_styles(svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return svc.pipeline.styles.list()


@router.get("/styles/{style_id}")
def get_style(style_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        style = svc.pipeline.styles.get(style_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    prompts = {}
    for name in ("system", "script", "scene"):
        try:
            text, origin = style.prompt_source(name)
            prompts[name] = {"text": text, "origin": origin}
        except FileNotFoundError:
            continue
    return {**style.public(), "data": style.data, "prompts": prompts,
            "override_dir": str(svc.settings.data_dir / "styles" / style_id)}


@router.get("/storyboard/schema")
def get_storyboard_schema() -> dict[str, Any]:
    return storyboard_json_schema()


# ----------------------------------------------------------------------------- settings


@router.get("/projects/{project_id}/settings")
def get_project_settings(project_id: str, svc: Services = Depends(get_services)) -> dict:
    _project_or_404(svc, project_id)
    effective = svc.pipeline.effective_settings(project_id, {})
    return {"settings": svc.pipeline.project_settings(project_id),
            "effective": effective.model_dump(), "defaults": svc.providers.defaults()}


@router.put("/projects/{project_id}/settings")
def put_project_settings(project_id: str, body: dict[str, Any],
                         svc: Services = Depends(get_services)) -> dict:
    _project_or_404(svc, project_id)
    try:
        saved = svc.pipeline.set_project_settings(project_id, body)
    except ValidationError as exc:
        raise _validation(exc) from exc
    return {"settings": saved}


@router.get("/settings/branding")
def get_branding(svc: Services = Depends(get_services)) -> dict:
    return _branding_out(svc, svc.pipeline.branding())


@router.put("/settings/branding")
def put_branding(body: dict[str, Any], svc: Services = Depends(get_services)) -> dict:
    return _branding_out(svc, svc.pipeline.set_branding(body))


def _branding_out(svc: Services, value: dict[str, Any]) -> dict[str, Any]:
    out = dict(value)
    out["logo_url"] = None
    if value.get("logo_asset_id"):
        with svc.session_factory() as s:
            asset = s.get(Asset, value["logo_asset_id"])
            if asset:
                out["logo_url"] = media_url(svc, asset.rel_path)
    return out


# ----------------------------------------------------------------------------- storyboards


class CreateStoryboard(BaseModel):
    document_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    save_as_project_default: bool = False


class UpdateStoryboard(BaseModel):
    data: dict[str, Any]
    settings: dict[str, Any] | None = None


class RegenerateScene(BaseModel):
    instruction: str = Field(default="", max_length=1000)


class RenderRequest(BaseModel):
    preview: bool = False
    # Different from the storyboard's aspect → a variant render (e.g. 9:16 for Reels).
    aspect: str | None = None


class TTSRequest(BaseModel):
    scene_ids: list[str] | None = None


def _storyboard_out(svc: Services, row: StoryboardRow) -> dict[str, Any]:
    assets = {}
    for scene_id, asset in (row.assets or {}).items():
        assets[scene_id] = {**{k: v for k, v in asset.items() if k != "words"},
                            "audio_url": media_url(svc, asset["audio"]),
                            "word_count": len(asset.get("words", []))}
    image_urls = {scene["id"]: media_url(svc, scene["visual"]["image"])
                  for scene in row.data.get("scenes", [])
                  if (scene.get("visual") or {}).get("image")}
    return {"id": row.id, "project_id": row.project_id, "document_id": row.document_id,
            "title": row.title, "data": row.data, "settings": row.settings, "assets": assets,
            "image_urls": image_urls, "created_at": row.created_at,
            "updated_at": row.updated_at}


@router.post("/projects/{project_id}/storyboards", status_code=202)
def create_storyboard(project_id: str, body: CreateStoryboard,
                      svc: Services = Depends(get_services)) -> dict:
    _project_or_404(svc, project_id)
    try:
        svc.pipeline.effective_settings(project_id, body.settings)
        svc.pipeline.styles.get(body.settings.get("style")
                                or svc.pipeline.effective_settings(project_id, {}).style)
    except ValidationError as exc:
        raise _validation(exc) from exc
    except KeyError as exc:
        raise HTTPException(422, str(exc.args[0])) from exc
    if body.save_as_project_default:
        keep = {k: v for k, v in body.settings.items()
                if k in ("style", "aspect", "target_duration_s", "language", "tone", "llm",
                         "tts", "alignment", "media", "music_asset_id", "music_volume")}
        svc.pipeline.set_project_settings(project_id, keep)
    job = svc.queue.enqueue("script", {"document_id": body.document_id,
                                       "settings": body.settings, "steps": SCRIPT_STEPS},
                            project_id=project_id)
    return job


@router.get("/projects/{project_id}/storyboards")
def list_storyboards(project_id: str, svc: Services = Depends(get_services)) -> list[dict]:
    with svc.session_factory() as s:
        rows = s.scalars(select(StoryboardRow).where(StoryboardRow.project_id == project_id)
                         .order_by(StoryboardRow.created_at.desc())).all()
        return [{"id": r.id, "title": r.title, "style": r.data.get("style"),
                 "aspect": r.data.get("aspect"), "scenes": len(r.data.get("scenes", [])),
                 "created_at": r.created_at, "updated_at": r.updated_at} for r in rows]


def _storyboard_row(svc: Services, storyboard_id: str) -> StoryboardRow:
    with svc.session_factory() as s:
        row = s.get(StoryboardRow, storyboard_id)
        if row is None:
            raise HTTPException(404, "Không tìm thấy storyboard")
        s.expunge(row)
        return row


@router.get("/storyboards/{storyboard_id}")
def get_storyboard(storyboard_id: str, svc: Services = Depends(get_services)) -> dict:
    return _storyboard_out(svc, _storyboard_row(svc, storyboard_id))


@router.put("/storyboards/{storyboard_id}")
def update_storyboard(storyboard_id: str, body: UpdateStoryboard,
                      svc: Services = Depends(get_services)) -> dict:
    _storyboard_row(svc, storyboard_id)
    try:
        row = svc.pipeline.update_storyboard(storyboard_id, body.data, body.settings)
    except ValidationError as exc:
        raise _validation(exc) from exc
    return _storyboard_out(svc, row)


@router.delete("/storyboards/{storyboard_id}", status_code=204)
def delete_storyboard(storyboard_id: str, svc: Services = Depends(get_services)) -> None:
    with svc.session_factory.begin() as s:
        row = s.get(StoryboardRow, storyboard_id)
        if row is None:
            raise HTTPException(404, "Không tìm thấy storyboard")
        s.delete(row)


@router.post("/storyboards/{storyboard_id}/scenes/{scene_id}/regenerate", status_code=202)
def regenerate_scene(storyboard_id: str, scene_id: str, body: RegenerateScene,
                     svc: Services = Depends(get_services)) -> dict:
    row = _storyboard_row(svc, storyboard_id)
    if scene_id not in {s["id"] for s in row.data.get("scenes", [])}:
        raise HTTPException(404, "Không có cảnh này")
    return svc.queue.enqueue("scene_regen", {"storyboard_id": storyboard_id,
                                             "scene_id": scene_id,
                                             "instruction": body.instruction},
                             project_id=row.project_id)


@router.post("/storyboards/{storyboard_id}/tts", status_code=202)
def synthesize(storyboard_id: str, body: TTSRequest | None = None,
               svc: Services = Depends(get_services)) -> dict:
    row = _storyboard_row(svc, storyboard_id)
    return svc.queue.enqueue("tts", {"storyboard_id": storyboard_id,
                                     "scene_ids": (body.scene_ids if body else None)},
                             project_id=row.project_id)


@router.post("/storyboards/{storyboard_id}/render", status_code=202)
def render(storyboard_id: str, body: RenderRequest | None = None,
           svc: Services = Depends(get_services)) -> dict:
    row = _storyboard_row(svc, storyboard_id)
    preview = bool(body and body.preview)
    try:
        render_row = svc.pipeline.create_render(storyboard_id, preview=preview,
                                                aspect=body.aspect if body else None)
    except lifecycle.LifecycleError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    job = svc.queue.enqueue("render", {"render_id": render_row.id, "storyboard_id": storyboard_id,
                                       "steps": RENDER_STEPS}, project_id=row.project_id)
    with svc.session_factory.begin() as s:
        r = s.get(Render, render_row.id)
        if r is not None:
            r.job_id = job["id"]
    return {"job": job, "render_id": render_row.id}


# ----------------------------------------------------------------------------- renders


def _render_out(svc: Services, r: Render) -> dict[str, Any]:
    urls = {k: media_url(svc, v) for k, v in (r.outputs or {}).items()}
    folder = None
    if r.outputs.get("video"):
        folder = str((svc.settings.data_dir / r.outputs["video"]).parent)
    return {"id": r.id, "project_id": r.project_id, "storyboard_id": r.storyboard_id,
            "job_id": r.job_id, "status": r.status, "style": r.style, "aspect": r.aspect,
            "settings": r.settings, "outputs": r.outputs, "urls": urls, "folder": folder,
            "duration_s": r.duration_s, "size_bytes": r.size_bytes, "error": r.error,
            "created_at": r.created_at, "finished_at": r.finished_at}


@router.get("/projects/{project_id}/renders")
def list_renders(project_id: str, svc: Services = Depends(get_services)) -> list[dict]:
    with svc.session_factory() as s:
        rows = s.scalars(select(Render).where(Render.project_id == project_id)
                         .order_by(Render.created_at.desc())).all()
        return [_render_out(svc, r) for r in rows]


@router.get("/renders/{render_id}")
def get_render(render_id: str, svc: Services = Depends(get_services)) -> dict:
    with svc.session_factory() as s:
        r = s.get(Render, render_id)
        if r is None:
            raise HTTPException(404, "Không tìm thấy bản xuất")
        return _render_out(svc, r)


@router.delete("/renders/{render_id}", status_code=204)
def delete_render(render_id: str, svc: Services = Depends(get_services)) -> None:
    with svc.session_factory.begin() as s:
        r = s.get(Render, render_id)
        if r is None:
            raise HTTPException(404, "Không tìm thấy bản xuất")
        if r.status in ("queued", "running"):
            raise HTTPException(409, "Bản xuất đang chạy; huỷ công việc trước khi xoá")
        if r.storyboard_id:
            sb = s.get(StoryboardRow, r.storyboard_id)
            if sb is not None and sb.approved_render_id == r.id:
                raise HTTPException(409, "Đây là bản đã duyệt; bỏ duyệt trước khi xoá")
        if s.scalars(select(Publication.id).where(
                Publication.render_id == r.id,
                Publication.status.in_(lifecycle.ACTIVE_PUBLICATION))).first():
            raise HTTPException(409, "Bản xuất đang được lên lịch đăng")
        for rel in (r.outputs or {}).values():
            path = svc.settings.data_dir / rel
            if path.is_file():
                path.unlink()
        s.delete(r)


def open_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    else:
        subprocess.Popen(["xdg-open", str(path)], creationflags=CREATE_NO_WINDOW,  # noqa: S603, S607
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@router.post("/renders/{render_id}/open-folder")
def open_folder(render_id: str, svc: Services = Depends(get_services)) -> dict:
    with svc.session_factory() as s:
        r = s.get(Render, render_id)
        if r is None:
            raise HTTPException(404, "Không tìm thấy bản xuất")
        folder = svc.settings.project_dir(r.project_id) / "outputs"
    folder.mkdir(parents=True, exist_ok=True)
    opened = True
    try:
        open_in_file_manager(folder)
    except OSError:
        opened = False
    return {"path": str(folder), "opened": opened}


# ----------------------------------------------------------------------------- assets


def _asset_out(svc: Services, a: Asset) -> dict[str, Any]:
    return {"id": a.id, "project_id": a.project_id, "kind": a.kind, "filename": a.filename,
            "rel_path": a.rel_path, "mime": a.mime, "size_bytes": a.size_bytes,
            "url": media_url(svc, a.rel_path), "created_at": a.created_at}


@router.post("/assets", status_code=201)
async def upload_asset(
    file: UploadFile = File(...),
    kind: Literal["image", "logo", "audio"] = Form(...),
    project_id: str | None = Form(default=None),
    svc: Services = Depends(get_services),
) -> dict:
    filename = safe_filename(file.filename or "file")
    ext = Path(filename).suffix.lower()
    if ext not in ASSET_KINDS[kind]:
        raise HTTPException(415, f"Định dạng {ext or '?'} không hỗ trợ cho {kind}")
    if project_id:
        _project_or_404(svc, project_id)
    data = await file.read(MAX_ASSET_BYTES + 1)
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(413, "Tệp quá lớn (tối đa 200 MB)")
    with svc.session_factory.begin() as s:
        asset = Asset(project_id=project_id, kind=kind, filename=filename, rel_path="",
                      mime=file.content_type, size_bytes=len(data))
        s.add(asset)
        s.flush()
        base = (svc.settings.project_dir(project_id) / "assets" if project_id
                else svc.settings.data_dir / "assets")
        dest = base / asset.id / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        asset.rel_path = dest.relative_to(svc.settings.data_dir).as_posix()
        s.flush()
        return _asset_out(svc, asset)


@router.get("/assets")
def list_assets(project_id: str | None = None, kind: str | None = None,
                svc: Services = Depends(get_services)) -> list[dict]:
    with svc.session_factory() as s:
        stmt = select(Asset).order_by(Asset.created_at.desc())
        if project_id:
            stmt = stmt.where((Asset.project_id == project_id) | Asset.project_id.is_(None))
        if kind:
            stmt = stmt.where(Asset.kind == kind)
        return [_asset_out(svc, a) for a in s.scalars(stmt)]


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: str, svc: Services = Depends(get_services)) -> None:
    with svc.session_factory.begin() as s:
        a = s.get(Asset, asset_id)
        if a is None:
            raise HTTPException(404, "Không tìm thấy tệp")
        folder = (svc.settings.data_dir / a.rel_path).parent
        shutil.rmtree(folder, ignore_errors=True)
        s.delete(a)


@router.get("/generation/defaults")
def generation_defaults(svc: Services = Depends(get_services)) -> dict:
    return {"settings": GenerationSettings().model_dump(), "defaults": svc.providers.defaults(),
            "render_steps": RENDER_STEPS, "script_steps": SCRIPT_STEPS,
            "updated_at": utcnow()}
