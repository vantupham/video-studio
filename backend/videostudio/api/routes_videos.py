"""Video library and review workflow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from videostudio.db import Project, Publication, Render, VideoEvent
from videostudio.db import Storyboard as StoryboardRow
from videostudio.review import lifecycle
from videostudio.review.compare import diff_snapshots

from .media import media_url
from .routes_pipeline import _render_out
from .services import Services, get_services

router = APIRouter(tags=["videos"])


class ActionRequest(BaseModel):
    action: str
    note: str = Field(default="", max_length=4000)


def _row_or_404(s, video_id: str) -> StoryboardRow:
    row = s.get(StoryboardRow, video_id)
    if row is None:
        raise HTTPException(404, "Không tìm thấy video")
    return row


def _lifecycle_error(exc: lifecycle.LifecycleError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


def _publication_brief(p: Publication) -> dict[str, Any]:
    return {"id": p.id, "platform": p.platform, "publisher_id": p.publisher_id,
            "account_id": p.account_id, "status": p.status, "scheduled_at": p.scheduled_at,
            "remote_url": p.remote_url, "progress": p.progress, "error": p.error,
            "published_at": p.published_at}


def _event_out(e: VideoEvent) -> dict[str, Any]:
    return {"id": e.id, "ts": e.ts, "action": e.action, "from_status": e.from_status,
            "to_status": e.to_status, "actor": e.actor, "note": e.note,
            "render_id": e.render_id, "data": e.data}


def _video_brief(svc: Services, row: StoryboardRow, project_name: str | None,
                 renders: list[Render], pubs: list[Publication]) -> dict[str, Any]:
    main = [r for r in renders if not (r.settings or {}).get("preview")]
    done = [r for r in main if r.status == "done"]
    latest = done[0] if done else None
    current = lifecycle.content_hash(row.data, row.settings)
    thumb = None
    if latest and latest.outputs.get("thumbnail"):
        thumb = media_url(svc, latest.outputs["thumbnail"])
    custom_thumb = (row.publish_meta or {}).get("thumbnail", {}).get("path")
    if custom_thumb:
        thumb = media_url(svc, custom_thumb)
    return {
        "id": row.id, "project_id": row.project_id, "project_name": project_name,
        "title": row.title, "status": row.status,
        "status_label": lifecycle.STATE_LABELS.get(row.status, row.status),
        "status_changed_at": row.status_changed_at or row.created_at,
        "style": row.data.get("style"), "aspect": row.data.get("aspect"),
        "scenes": len(row.data.get("scenes", [])),
        "duration_s": latest.duration_s if latest else None,
        "thumbnail_url": thumb,
        "latest_render_id": latest.id if latest else None,
        "approved_render_id": row.approved_render_id,
        "outdated": bool(latest and (latest.settings or {}).get("content_hash")
                         not in (None, current)),
        "renders": len(done), "rendering": any(r.status in ("queued", "running") for r in main),
        "publications": [_publication_brief(p) for p in pubs if p.status != "cancelled"],
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


@router.get("/videos")
def list_videos(status: str | None = None, project_id: str | None = None,
                q: str | None = None, platform: str | None = None,
                svc: Services = Depends(get_services)) -> dict[str, Any]:
    with svc.session_factory() as s:
        stmt = select(StoryboardRow, Project.name).join(
            Project, Project.id == StoryboardRow.project_id)
        if project_id:
            stmt = stmt.where(StoryboardRow.project_id == project_id)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(StoryboardRow.title.ilike(like), Project.name.ilike(like)))
        if platform:
            stmt = stmt.where(StoryboardRow.id.in_(
                select(Publication.storyboard_id).where(Publication.platform == platform,
                                                        Publication.status != "cancelled")))
        counts_rows = s.execute(stmt.with_only_columns(StoryboardRow.status, func.count())
                                .group_by(StoryboardRow.status)).all()
        if status:
            stmt = stmt.where(StoryboardRow.status.in_(status.split(",")))
        rows = s.execute(stmt.order_by(func.coalesce(StoryboardRow.status_changed_at,
                                                     StoryboardRow.created_at).desc())).all()
        ids = [r.id for r, _ in rows]
        renders: dict[str, list[Render]] = {i: [] for i in ids}
        pubs: dict[str, list[Publication]] = {i: [] for i in ids}
        if ids:
            for r in s.scalars(select(Render).where(Render.storyboard_id.in_(ids))
                               .order_by(Render.created_at.desc())):
                renders[r.storyboard_id].append(r)  # type: ignore[index]
            for p in s.scalars(select(Publication).where(Publication.storyboard_id.in_(ids))
                               .order_by(Publication.created_at)):
                pubs[p.storyboard_id].append(p)
        items = [_video_brief(svc, row, name, renders[row.id], pubs[row.id])
                 for row, name in rows]
    counts = {st: 0 for st in lifecycle.STATES}
    counts.update({st: n for st, n in counts_rows})
    return {"items": items, "counts": counts,
            "states": [{"id": st, "label": lifecycle.STATE_LABELS[st]}
                       for st in lifecycle.STATES]}


@router.get("/videos/{video_id}")
def get_video(video_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    with svc.session_factory() as s:
        row = _row_or_404(s, video_id)
        project = s.get(Project, row.project_id)
        renders = list(s.scalars(select(Render).where(Render.storyboard_id == video_id)
                                 .order_by(Render.created_at.desc())))
        pubs = list(s.scalars(select(Publication).where(Publication.storyboard_id == video_id)
                              .order_by(Publication.created_at)))
        events = list(s.scalars(select(VideoEvent).where(VideoEvent.storyboard_id == video_id)
                                .order_by(VideoEvent.id.desc()).limit(200)))
        current = lifecycle.content_hash(row.data, row.settings)
        out = _video_brief(svc, row, project.name if project else None, renders, pubs)
        version = 0
        versions = []
        for r in reversed(renders):
            info = _render_out(svc, r)
            settings = r.settings or {}
            if not settings.get("preview") and not settings.get("variant") \
                    and r.status == "done":
                version += 1
                info["version"] = version
            info["kind"] = "preview" if settings.get("preview") else \
                "variant" if settings.get("variant") else "main"
            info["content_hash"] = settings.get("content_hash")
            info["current"] = settings.get("content_hash") == current
            info["approved_content"] = bool(row.approved_hash) and \
                settings.get("content_hash") == row.approved_hash
            info["timeline"] = settings.get("timeline", [])
            info.pop("settings", None)
            versions.append(info)
        out.update({
            "content_hash": current, "approved_hash": row.approved_hash,
            "actions": lifecycle.available_actions(row.status),
            "render_locked": row.status in lifecycle.RENDER_LOCKED,
            "renders": list(reversed(versions)),
            "events": [_event_out(e) for e in events],
            "publish_meta": row.publish_meta or {},
        })
        return out


@router.post("/videos/{video_id}/actions")
def video_action(video_id: str, body: ActionRequest,
                 svc: Services = Depends(get_services)) -> dict[str, Any]:
    with svc.session_factory.begin() as s:
        row = _row_or_404(s, video_id)
        try:
            ev = lifecycle.apply_action(s, row, body.action, body.note)
        except lifecycle.LifecycleError as exc:
            raise _lifecycle_error(exc) from exc
        s.flush()
        event = _event_out(ev)
        status = row.status
    svc.bus.publish({"type": "video", "id": video_id, "status": status, "event": event})
    return get_video(video_id, svc)


@router.get("/videos/{video_id}/events")
def video_events(video_id: str, svc: Services = Depends(get_services)) -> list[dict]:
    with svc.session_factory() as s:
        _row_or_404(s, video_id)
        return [_event_out(e) for e in s.scalars(
            select(VideoEvent).where(VideoEvent.storyboard_id == video_id)
            .order_by(VideoEvent.id.desc()))]


@router.get("/videos/{video_id}/compare")
def compare_renders(video_id: str, a: str, b: str = "current",
                    svc: Services = Depends(get_services)) -> dict[str, Any]:
    """Scene diff between render ``a`` and render ``b`` (or the current storyboard)."""
    with svc.session_factory() as s:
        row = _row_or_404(s, video_id)

        def snapshot(rid: str) -> dict[str, Any]:
            if rid == "current":
                return {"title": row.title, "scenes": [
                    {"id": sc["id"], "narration": sc.get("narration", ""),
                     "duration": sc.get("duration", "auto"),
                     "visual": {k: v for k, v in (sc.get("visual") or {}).items()
                                if v is not None},
                     "overlays": sc.get("overlays", [])}
                    for sc in row.data.get("scenes", [])]}
            r = s.get(Render, rid)
            if r is None or r.storyboard_id != video_id:
                raise HTTPException(404, "Không tìm thấy bản dựng")
            snap = (r.settings or {}).get("snapshot")
            if snap is None:
                raise HTTPException(422, "Bản dựng này được tạo trước khi có lịch sử phiên bản")
            return snap

        return diff_snapshots(snapshot(a), snapshot(b))
