"""Video lifecycle (a "video" is a storyboard row) and its history log.

draft → generating → rendered → in_review → changes_requested ↺ → approved → scheduled →
publishing → published | failed

Manual actions go through :data:`ACTIONS`. Render jobs and publications move the status
via :func:`on_render_started` / :func:`on_render_finished` / :func:`sync_publications`.
Every change is written to ``video_events``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from videostudio.db import Publication, Render, Storyboard, VideoEvent, utcnow

STATES = ("draft", "generating", "rendered", "in_review", "changes_requested", "approved",
          "scheduled", "publishing", "published", "failed")

STATE_LABELS = {
    "draft": "Bản nháp",
    "generating": "Đang dựng",
    "rendered": "Đã dựng",
    "in_review": "Chờ duyệt",
    "changes_requested": "Cần sửa",
    "approved": "Đã duyệt",
    "scheduled": "Đã lên lịch",
    "publishing": "Đang đăng",
    "published": "Đã đăng",
    "failed": "Lỗi đăng",
}

PUBLISH_STATES = ("approved", "scheduled", "publishing", "published", "failed")


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    sources: tuple[str, ...]
    target: str
    note_required: bool = False


ACTIONS: dict[str, Action] = {a.id: a for a in (
    Action("submit", "Gửi duyệt", ("rendered",), "in_review"),
    Action("approve", "Duyệt", ("rendered", "in_review"), "approved"),
    Action("request_changes", "Yêu cầu sửa", ("rendered", "in_review", "approved"),
           "changes_requested", note_required=True),
    Action("reopen", "Bỏ duyệt", ("approved", "published", "failed"), "in_review"),
)}

# Statuses in which a new main render is refused (a publication is in flight).
RENDER_LOCKED = ("scheduled", "publishing")

ACTIVE_PUBLICATION = ("queued", "retry_wait", "uploading", "processing", "scheduled",
                      "scheduled_remote", "missed")


class LifecycleError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def content_hash(data: dict[str, Any], settings: dict[str, Any] | None) -> str:
    """Hash of everything that shapes the video except the aspect ratio, so a 9:16 variant
    of approved 16:9 content counts as approved too."""
    story = {k: v for k, v in (data or {}).items() if k != "aspect"}
    gen = {k: v for k, v in (settings or {}).items() if k not in ("aspect",)}
    raw = json.dumps([story, gen], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def log_event(s: Session, row: Storyboard, action: str, to_status: str | None = None,
              *, note: str = "", actor: str = "user", render_id: str | None = None,
              data: dict[str, Any] | None = None) -> VideoEvent:
    from_status = row.status
    if to_status and to_status != row.status:
        row.status = to_status
        row.status_changed_at = utcnow()
    ev = VideoEvent(storyboard_id=row.id, action=action, from_status=from_status,
                    to_status=to_status or from_status, actor=actor, note=note or "",
                    render_id=render_id, data=data or {})
    s.add(ev)
    return ev


def available_actions(status: str) -> list[dict[str, Any]]:
    return [{"id": a.id, "label": a.label, "note_required": a.note_required}
            for a in ACTIONS.values() if status in a.sources]


def latest_main_render(s: Session, storyboard_id: str) -> Render | None:
    rows = s.scalars(select(Render).where(Render.storyboard_id == storyboard_id,
                                          Render.status == "done")
                     .order_by(Render.created_at.desc())).all()
    for r in rows:
        if not (r.settings or {}).get("preview") and not (r.settings or {}).get("variant"):
            return r
    return None


def apply_action(s: Session, row: Storyboard, action_id: str, note: str = "",
                 actor: str = "user") -> VideoEvent:
    action = ACTIONS.get(action_id)
    if action is None:
        raise LifecycleError(f"Thao tác không hợp lệ: {action_id}", 422)
    if row.status not in action.sources:
        raise LifecycleError(
            f"Không thể '{action.label}' khi video đang ở trạng thái "
            f"'{STATE_LABELS.get(row.status, row.status)}'")
    note = (note or "").strip()
    if action.note_required and not note:
        raise LifecycleError("Hãy ghi chú cần sửa gì", 422)
    render_id = None
    data: dict[str, Any] = {}
    if action_id == "approve":
        render = latest_main_render(s, row.id)
        if render is None:
            raise LifecycleError("Chưa có bản dựng hoàn chỉnh để duyệt")
        current = content_hash(row.data, row.settings)
        if (render.settings or {}).get("content_hash") not in (None, current):
            raise LifecycleError("Storyboard đã sửa sau lần dựng gần nhất. Hãy dựng lại "
                                 "trước khi duyệt.")
        row.approved_render_id = render.id
        row.approved_hash = (render.settings or {}).get("content_hash") or current
        render_id = render.id
    elif action_id in ("request_changes", "reopen"):
        if row.status == "approved" or action_id == "reopen":
            data["previous_approval"] = row.approved_render_id
        if s.scalars(select(Publication.id).where(
                Publication.storyboard_id == row.id,
                Publication.status.in_(("uploading", "processing")))).first():
            raise LifecycleError("Video đang được tải lên; đợi xong hoặc huỷ trước")
        row.approved_render_id = None
        row.approved_hash = None
    return log_event(s, row, action_id, action.target, note=note, actor=actor,
                     render_id=render_id, data=data)


# ------------------------------------------------------------------ renders


def on_render_created(s: Session, row: Storyboard, *, variant: bool, preview: bool) -> None:
    if preview or variant:
        return
    if row.status in RENDER_LOCKED:
        raise LifecycleError("Video đang chờ đăng hoặc đang đăng. Huỷ lịch đăng trước khi "
                             "dựng lại.")


def on_render_started(s: Session, row: Storyboard, render: Render) -> None:
    settings = render.settings or {}
    if settings.get("preview") or settings.get("variant"):
        return
    if row.status == "generating":
        return
    log_event(s, row, "render_started", "generating", actor="system", render_id=render.id,
              data={"previous": row.status})


def on_render_finished(s: Session, row: Storyboard, render: Render, ok: bool,
                       error: str | None = None) -> None:
    settings = render.settings or {}
    if settings.get("preview") or settings.get("variant"):
        if settings.get("variant"):
            log_event(s, row, "variant_rendered" if ok else "variant_failed", actor="system",
                      render_id=render.id, note=error or "",
                      data={"aspect": render.aspect})
        return
    if row.status != "generating":
        return
    if ok:
        log_event(s, row, "render_done", "rendered", actor="system", render_id=render.id,
                  data={"duration_s": render.duration_s})
        return
    previous = "draft"
    started = s.scalars(select(VideoEvent).where(
        VideoEvent.storyboard_id == row.id, VideoEvent.action == "render_started")
        .order_by(VideoEvent.id.desc())).first()
    if started is not None:
        previous = (started.data or {}).get("previous") or started.from_status or "draft"
        if previous == "generating":
            previous = "draft"
    log_event(s, row, "render_failed", previous, actor="system", render_id=render.id,
              note=error or "")


def reset_stale_generating(s: Session) -> int:
    """After a crash no render runs: put 'generating' videos back where they were."""
    n = 0
    for row in s.scalars(select(Storyboard).where(Storyboard.status == "generating")):
        running = s.scalars(select(Render.id).where(
            Render.storyboard_id == row.id, Render.status.in_(("queued", "running")))).first()
        if running:
            continue
        has_render = latest_main_render(s, row.id) is not None
        log_event(s, row, "render_interrupted", "rendered" if has_render else "draft",
                  actor="system")
        n += 1
    return n


# ------------------------------------------------------------------ publications


def aggregate_publication_status(statuses: list[str]) -> str | None:
    live = [st for st in statuses if st != "cancelled"]
    if not live:
        return None
    if any(st in ("uploading", "processing", "queued", "retry_wait") for st in live):
        return "publishing"
    if any(st == "failed" for st in live):
        return "failed"
    if any(st in ("scheduled", "scheduled_remote", "missed") for st in live):
        return "scheduled"
    return "published"


def sync_publications(s: Session, row: Storyboard, note: str = "") -> str:
    """Derive the video status from its publications (called after each change)."""
    if row.status not in PUBLISH_STATES:
        return row.status
    statuses = list(s.scalars(select(Publication.status)
                              .where(Publication.storyboard_id == row.id)))
    target = aggregate_publication_status(statuses) or "approved"
    if target != row.status:
        log_event(s, row, f"publication_{target}", target, actor="system", note=note)
    return row.status
