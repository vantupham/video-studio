"""Publishing API: per-video metadata, validation, publications, schedules, queue state,
desktop preferences, notifications and the mock post page."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from videostudio.db import Render
from videostudio.db import Storyboard as StoryboardRow
from videostudio.pipeline.models import GenerationSettings
from videostudio.providers.errors import ProviderError
from videostudio.providers.service import ProviderNotFound
from videostudio.publishing.metadata import suggest_metadata
from videostudio.publishing.scheduler import desktop_prefs, set_desktop_prefs
from videostudio.publishing.service import PublishServiceError

from .media import media_key, media_url
from .services import Services, get_services

router = APIRouter(tags=["publish"])
public_router = APIRouter(tags=["publish"])

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


class ScheduleIn(BaseModel):
    mode: str = "now"
    at: datetime | None = None


class ValidateIn(BaseModel):
    account_id: str
    options: dict[str, Any] = Field(default_factory=dict)
    schedule: ScheduleIn | None = None
    render_id: str | None = None


class PublicationItem(ValidateIn):
    allow_repost: bool = False


class CreatePublications(BaseModel):
    items: list[PublicationItem]


class SuggestIn(BaseModel):
    platform: str = "youtube"


class ThumbnailIn(BaseModel):
    mode: str | None = None
    frame_s: float | None = None
    show_title: bool | None = None
    text: str | None = None
    render_id: str | None = None


class RescheduleIn(BaseModel):
    at: datetime


class ResolveMissed(BaseModel):
    ids: list[str]
    action: str
    at: datetime | None = None


class QueueStateIn(BaseModel):
    paused: bool


def _err(exc: PublishServiceError) -> HTTPException:
    if exc.issues:
        return HTTPException(exc.status_code, {"message": exc.message, "issues": exc.issues})
    return HTTPException(exc.status_code, exc.message)


def _video(svc: Services, video_id: str) -> StoryboardRow:
    with svc.session_factory() as s:
        row = s.get(StoryboardRow, video_id)
        if row is None:
            raise HTTPException(404, "Không tìm thấy video")
        s.expunge(row)
        return row


def _publication(svc: Services, p: dict[str, Any]) -> dict[str, Any]:
    thumb = (p.get("meta") or {}).get("thumbnail")
    if thumb:
        p["thumbnail_url"] = media_url(svc, thumb)
    return p


# ------------------------------------------------------------------ per video


@router.get("/videos/{video_id}/publish-meta")
def get_meta(video_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.publish.meta(video_id)
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.put("/videos/{video_id}/publish-meta")
def put_meta(video_id: str, body: dict[str, Any],
             svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.publish.set_meta(video_id, body)
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.post("/videos/{video_id}/validate")
def validate_publication(video_id: str, body: ValidateIn,
                         svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.publish.check(video_id, body.account_id, options=body.options,
                                 schedule=svc.publish._schedule(
                                     body.schedule.model_dump() if body.schedule else None),
                                 render_id=body.render_id)
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.post("/videos/{video_id}/publications", status_code=201)
def create_publications(video_id: str, body: CreatePublications,
                        svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    items = [{**i.model_dump(exclude={"schedule"}),
              "schedule": i.schedule.model_dump() if i.schedule else None} for i in body.items]
    try:
        return [_publication(svc, p) for p in svc.publish.create(video_id, items)]
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.get("/videos/{video_id}/publications")
def video_publications(video_id: str, svc: Services = Depends(get_services)) -> list[dict]:
    return [_publication(svc, p) for p in svc.publish.list(video_id=video_id)]


@router.post("/videos/{video_id}/suggest-metadata")
async def suggest(video_id: str, body: SuggestIn,
                  svc: Services = Depends(get_services)) -> dict[str, Any]:
    row = _video(svc, video_id)
    gen = GenerationSettings.model_validate(row.settings or {})
    try:
        llm = svc.pipeline._resolve("llm", gen, svc.pipeline.project_settings(row.project_id))
    except (ProviderNotFound, KeyError) as exc:
        raise HTTPException(422, "Chưa cấu hình mô hình ngôn ngữ (LLM)") from exc
    with svc.session_factory() as s:
        render = s.get(Render, row.approved_render_id) if row.approved_render_id else None
        duration = render.duration_s if render else None
    try:
        return await suggest_metadata(llm, platform=body.platform, title=row.title,
                                      aspect=row.data.get("aspect", "16:9"),
                                      duration_s=duration, storyboard=row.data,
                                      language=row.data.get("language", "vi"))
    except ProviderError as exc:
        raise HTTPException(502, exc.message) from exc


def _render_for_thumb(svc: Services, row: StoryboardRow, render_id: str | None) -> Render:
    with svc.session_factory() as s:
        rid = render_id or row.approved_render_id
        render = s.get(Render, rid) if rid else None
        if render is None:
            from videostudio.review.lifecycle import latest_main_render

            render = latest_main_render(s, row.id)
        if render is None or render.storyboard_id != row.id:
            raise HTTPException(422, "Chưa có bản dựng để tạo ảnh thu nhỏ")
        s.expunge(render)
        return render


@router.post("/videos/{video_id}/thumbnail")
def thumbnail(video_id: str, body: ThumbnailIn,
              svc: Services = Depends(get_services)) -> dict[str, Any]:
    """Update thumbnail settings (if given) and return the generated thumbnail."""
    patch = body.model_dump(exclude={"render_id"}, exclude_unset=True)
    if patch:
        svc.publish.set_meta(video_id, {"thumbnail": patch})
    row = _video(svc, video_id)
    render = _render_for_thumb(svc, row, body.render_id)
    try:
        path = svc.publish.thumbnail_for(row, render, force=True)
    except Exception as exc:  # noqa: BLE001 - ffmpeg / image errors
        raise HTTPException(500, f"Không tạo được ảnh thu nhỏ: {exc}") from exc
    rel = path.relative_to(svc.settings.data_dir).as_posix() if path else None
    return {"thumbnail": svc.publish.meta(video_id)["thumbnail"],
            "url": media_url(svc, rel) if rel else None, "aspect": render.aspect}


@router.post("/videos/{video_id}/thumbnail/upload")
async def upload_thumbnail(video_id: str, file: UploadFile = File(...),
                           svc: Services = Depends(get_services)) -> dict[str, Any]:
    row = _video(svc, video_id)
    name = file.filename or "thumb.png"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in IMAGE_EXTS:
        raise HTTPException(415, "Ảnh thu nhỏ phải là PNG, JPG hoặc WebP")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Ảnh quá lớn (tối đa 20 MB)")
    dest_dir = svc.settings.project_dir(row.project_id) / "publish" / row.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob("custom-*"):
        old.unlink(missing_ok=True)
    dest = dest_dir / f"custom-source{ext}"
    dest.write_bytes(data)
    rel = dest.relative_to(svc.settings.data_dir).as_posix()
    svc.publish.set_meta(video_id, {"thumbnail": {"mode": "custom", "custom_path": rel}})
    return thumbnail(video_id, ThumbnailIn(), svc)


# ------------------------------------------------------------------ publications


@router.get("/publications")
def list_publications(status: str | None = None, video_id: str | None = None,
                      svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return [_publication(svc, p) for p in svc.publish.list(video_id=video_id, status=status)]


@router.get("/publications/{pub_id}")
def get_publication(pub_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return _publication(svc, svc.publish.get(pub_id))
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.post("/publications/{pub_id}/cancel")
def cancel_publication(pub_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.publish.cancel(pub_id)
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.post("/publications/{pub_id}/retry")
def retry_publication(pub_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.publish.retry(pub_id)
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.post("/publications/{pub_id}/reschedule")
def reschedule_publication(pub_id: str, body: RescheduleIn,
                           svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.publish.reschedule(pub_id, body.at)
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.post("/publications/{pub_id}/poll")
async def poll_publication(pub_id: str, svc: Services = Depends(get_services)) -> dict:
    await svc.publish.poll(pub_id)
    return svc.publish.get(pub_id)


@router.get("/schedule/missed")
def missed(svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return svc.publish.missed()


@router.post("/schedule/missed/resolve")
def resolve_missed(body: ResolveMissed, svc: Services = Depends(get_services)) -> list[dict]:
    try:
        return svc.publish.resolve_missed(body.ids, body.action, body.at)
    except PublishServiceError as exc:
        raise _err(exc) from exc


@router.get("/schedule/status")
def schedule_status(svc: Services = Depends(get_services)) -> dict[str, Any]:
    nxt = svc.publish.next_local_schedule()
    return {"next_local": nxt, "last_tick": svc.scheduler.last_tick,
            **svc.publish.queue_state()}


@router.get("/queue/state")
def get_queue_state(svc: Services = Depends(get_services)) -> dict[str, Any]:
    return svc.publish.queue_state()


@router.put("/queue/state")
def put_queue_state(body: QueueStateIn, svc: Services = Depends(get_services)) -> dict:
    return svc.publish.set_paused(body.paused)


@router.get("/settings/desktop")
def get_desktop(svc: Services = Depends(get_services)) -> dict[str, Any]:
    return desktop_prefs(svc.session_factory)


@router.put("/settings/desktop")
def put_desktop(body: dict[str, Any], svc: Services = Depends(get_services)) -> dict:
    return set_desktop_prefs(svc.session_factory, body)


@router.get("/notifications")
def notifications(svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return svc.publish.list_notifications()


@router.post("/notifications/read", status_code=204)
def notifications_read(svc: Services = Depends(get_services)) -> None:
    svc.publish.mark_read()


# ------------------------------------------------------------------ mock post page

PAGE = """<!doctype html><html lang="vi"><head><meta charset="utf-8">
<title>{title}</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;background:#0b1120;color:#e2e8f0;margin:0}}
main{{max-width:880px;margin:0 auto;padding:24px}}video{{width:100%;max-height:70vh;
background:#000;border-radius:12px}}.badge{{display:inline-block;padding:2px 10px;
border-radius:99px;background:{color};color:#fff;font-size:13px;font-weight:600}}
.muted{{color:#94a3b8;font-size:14px}}pre{{white-space:pre-wrap;font-family:inherit;
background:#111a2e;padding:12px;border-radius:8px}}.tag{{display:inline-block;margin:2px;
padding:2px 8px;border-radius:6px;background:#1e293b;font-size:13px}}</style></head>
<body><main><p><span class="badge">{platform} · GIẢ LẬP</span>
<span class="muted"> {account} · {when}</span></p><h1>{title}</h1>
<video src="{video}" controls poster="{poster}"></video>
<p class="muted">Mã bài: {id} · {privacy} · {aspect} · {duration}s · {size} MB ·
SHA-256 {sha}</p><pre>{description}</pre><div>{tags}</div>
<p class="muted">Đây là trang giả lập do Video Studio tạo để kiểm thử quy trình đăng; không
có gì được gửi lên mạng.</p></main></body></html>"""


@public_router.get("/mock-posts/{post_id}", response_class=HTMLResponse)
def mock_post(post_id: str, request: Request, k: str = Query(default="")) -> HTMLResponse:
    svc: Services = request.app.state.services
    if k != media_key(svc.settings.token):
        raise HTTPException(403, "Liên kết không hợp lệ")
    if not post_id.replace("-", "").isalnum():
        raise HTTPException(404)
    path = svc.settings.data_dir / "mock_posts" / f"{post_id}.json"
    if not path.is_file():
        raise HTTPException(404, "Không có bài đăng này")
    post = json.loads(path.read_text(encoding="utf-8"))
    data_dir = svc.settings.data_dir

    def url(p: str | None) -> str:
        if not p:
            return ""
        try:
            rel = Path(p).resolve().relative_to(data_dir.resolve())
        except ValueError:
            return ""
        return media_url(svc, rel.as_posix())

    youtube = post["platform"] == "youtube"
    when = post.get("scheduled_at") or post.get("created_at", "")
    page = PAGE.format(
        title=html.escape(post.get("title", "")), id=html.escape(post["id"]),
        platform="YouTube" if youtube else "Facebook",
        color="#dc2626" if youtube else "#2563eb",
        account=html.escape(post.get("account", "")),
        when=("Hẹn đăng " if post.get("scheduled_at") else "Đăng lúc ") + html.escape(when),
        video=html.escape(url(post.get("video"))),
        poster=html.escape(url(post.get("thumbnail"))),
        privacy=html.escape(str(post.get("privacy", ""))),
        aspect=html.escape(str(post.get("aspect", ""))),
        duration=f"{float(post.get('duration_s') or 0):.1f}",
        size=f"{int(post.get('size', 0)) / 1e6:.1f}", sha=post.get("sha256", "")[:12],
        description=html.escape(post.get("description", "")),
        tags="".join(f'<span class="tag">{html.escape(t)}</span>'
                     for t in post.get("tags", [])))
    return HTMLResponse(page)
