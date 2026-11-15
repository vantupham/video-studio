"""Publications: validation, creation, the publish job, retries, status polling, missed
schedules, notifications.

Idempotency: a publication row is the unit of posting. Its upload state (session URI,
offsets, remote id, finished post-steps) is persisted after every step, so re-running the
job resumes; a publication that has a remote id and reached ``published`` /
``scheduled_remote`` / ``processing`` is never uploaded again. Creating a second
publication of the same video to the same account is refused unless explicitly asked."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from videostudio.config import Settings
from videostudio.db import AppSetting, Publication, Render, SocialAccount, utcnow
from videostudio.db import Storyboard as StoryboardRow
from videostudio.jobs import EventBus, JobQueue
from videostudio.pipeline.styles import StyleLibrary
from videostudio.plugins.base import JobCancelled, JobContext
from videostudio.power import keep_awake
from videostudio.review import lifecycle

from . import thumbnail as thumbs
from .accounts import SocialService
from .base import PLATFORMS, PublishContext, Publisher, PublishError, PublishRequest, PublishResult
from .mock import MOCK_POST_PREFIX
from .validation import has_errors, validate

log = logging.getLogger(__name__)

PUBLISH_STEPS = [{"id": "upload", "label": "Tải lên", "start": 0, "end": 90},
                 {"id": "finish", "label": "Hoàn tất", "start": 90, "end": 100}]
MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 30
BACKOFF_MAX_S = 1800
POLL_PROCESSING_S = 30
POLL_MAX_AGE = timedelta(hours=24)
MISSED_GRACE_S = 120
YOUTUBE_DAILY_QUOTA = 10_000
YOUTUBE_UPLOAD_COST = 1600
PACIFIC = timezone(timedelta(hours=-8))  # quota day; DST ignored (±1h)

DEFAULT_META: dict[str, Any] = {
    "common": {"title": "", "description": "", "tags": [], "hashtags": []},
    "youtube": {"title": "", "description": "", "tags": [], "options": {}},
    "facebook": {"title": "", "description": "", "tags": [], "options": {}},
    "thumbnail": {"mode": "auto", "frame_s": None, "show_title": True, "text": "",
                  "custom_path": None},
}
FINAL = ("published", "cancelled", "failed")
NOT_REPOSTABLE = ("queued", "retry_wait", "uploading", "processing", "scheduled",
                  "scheduled_remote", "missed", "published")


class PublishServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400,
                 issues: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.issues = issues


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) \
            else v
    return out


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def backoff_s(attempts: int) -> int:
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** max(0, attempts - 1))


def next_quota_reset(now: datetime) -> datetime:
    local = now.astimezone(PACIFIC)
    reset = (local + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return reset.astimezone(UTC)


class PublishService:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session],
                 publishers: dict[str, Publisher], queue: JobQueue, bus: EventBus,
                 social: SocialService, styles: StyleLibrary,
                 mock_url: Callable[[str], str] | None = None):
        self.settings = settings
        self.sf = session_factory
        self.publishers = publishers
        self.queue = queue
        self.bus = bus
        self.social = social
        self.styles = styles
        self.mock_url = mock_url
        self.notifications: deque[dict[str, Any]] = deque(maxlen=100)
        self._notify_lock = threading.Lock()
        self._notify_seq = 0
        self.sleep = asyncio.sleep
        social.notify = self.notify

    # ------------------------------------------------------------------ notifications

    def notify(self, *, level: str = "info", title: str, message: str = "",
               link: str | None = None, data: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._notify_lock:
            self._notify_seq += 1
            item = {"id": self._notify_seq, "ts": utcnow().isoformat(), "level": level,
                    "title": title, "message": message, "link": link, "read": False,
                    "data": data or {}}
            self.notifications.appendleft(item)
        self.bus.publish({"type": "notification", **item})
        return item

    def list_notifications(self) -> list[dict[str, Any]]:
        return list(self.notifications)

    def mark_read(self) -> None:
        with self._notify_lock:
            for n in self.notifications:
                n["read"] = True

    # ------------------------------------------------------------------ queue state

    def queue_state(self) -> dict[str, Any]:
        with self.sf() as s:
            row = s.get(AppSetting, "queue_state")
            return {"paused": bool(row and row.value.get("paused"))}

    def set_paused(self, paused: bool) -> dict[str, Any]:
        with self.sf.begin() as s:
            row = s.get(AppSetting, "queue_state")
            if row is None:
                s.add(AppSetting(key="queue_state", value={"paused": paused}))
            else:
                row.value = {"paused": paused}
        if paused:
            self.queue.pause()
        else:
            self.queue.resume()
        self.bus.publish({"type": "queue", "paused": paused})
        return self.queue_state()

    # ------------------------------------------------------------------ metadata

    def meta(self, video_id: str) -> dict[str, Any]:
        with self.sf() as s:
            row = s.get(StoryboardRow, video_id)
            if row is None:
                raise PublishServiceError("Không tìm thấy video", 404)
            return _merge(DEFAULT_META, row.publish_meta or {})

    def set_meta(self, video_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_META)
        with self.sf.begin() as s:
            row = s.get(StoryboardRow, video_id)
            if row is None:
                raise PublishServiceError("Không tìm thấy video", 404)
            current = _merge(DEFAULT_META, row.publish_meta or {})
            merged = _merge(current, {k: v for k, v in patch.items() if k in allowed})
            for platform in PLATFORMS:
                if "options" in (patch.get(platform) or {}):
                    merged[platform]["options"] = dict(patch[platform]["options"] or {})
            row.publish_meta = merged
            return merged

    def resolved_meta(self, row: StoryboardRow, platform: str,
                      publisher: Publisher, options: dict[str, Any] | None = None
                      ) -> dict[str, Any]:
        meta = _merge(DEFAULT_META, row.publish_meta or {})
        common, per = meta["common"], meta.get(platform, {})
        title = (per.get("title") or common.get("title") or row.title or "").strip()
        description = (per.get("description") or common.get("description") or "").strip()
        hashtags = [h for h in common.get("hashtags", []) if h and h not in description]
        if hashtags:
            description = (description + "\n\n" + " ".join(hashtags)).strip()
        tags = per.get("tags") or common.get("tags") or []
        opts = {**publisher.default_options(), **(per.get("options") or {}), **(options or {})}
        return {"title": title, "description": description, "tags": list(tags),
                "options": opts}

    # ------------------------------------------------------------------ thumbnail

    def _thumb_dir(self, row: StoryboardRow) -> Path:
        return self.settings.project_dir(row.project_id) / "publish" / row.id

    def thumbnail_for(self, row: StoryboardRow, render: Render, force: bool = False
                      ) -> Path | None:
        cfg = _merge(DEFAULT_META, row.publish_meta or {})["thumbnail"]
        mode = cfg.get("mode", "auto")
        if mode == "none":
            return None
        out_dir = self._thumb_dir(row)
        if mode == "custom" and cfg.get("custom_path"):
            src = self.settings.data_dir / cfg["custom_path"]
            if src.is_file():
                dest = out_dir / f"custom-{render.aspect.replace(':', 'x')}.jpg"
                if force or not dest.is_file() or dest.stat().st_mtime < src.stat().st_mtime:
                    thumbs.prepare_custom(src, dest, render.aspect)
                return dest
        video = self.settings.data_dir / (render.outputs or {}).get("video", "")
        if not video.is_file():
            return None
        duration = render.duration_s or 1.0
        at = cfg.get("frame_s")
        if at is None:
            at = self._default_frame(render)
        at = max(0.0, min(float(at), duration - 0.1))
        text = (cfg.get("text") or row.title or "").strip()
        key = f"{render.id}-{at:.2f}-{int(bool(cfg.get('show_title', True)))}-" \
              f"{abs(hash(text)) % 10**8}"
        dest = out_dir / f"auto-{key}.jpg"
        if force or not dest.is_file():
            for old in out_dir.glob(f"auto-{render.id}-*.jpg"):
                old.unlink(missing_ok=True)
            style = self.styles.get(render.style)
            thumbs.make_thumbnail(video, dest, text, style, render.aspect, at,
                                  bool(cfg.get("show_title", True)))
        return dest

    @staticmethod
    def _default_frame(render: Render) -> float:
        """Middle of the first image scene (else 40% into the second scene)."""
        timeline = (render.settings or {}).get("timeline") or []
        snap = {sc["id"]: sc for sc in ((render.settings or {}).get("snapshot") or {}).get(
            "scenes", [])}
        for t in timeline:
            visual = (snap.get(t["scene_id"]) or {}).get("visual") or {}
            if visual.get("type") in ("image", "stock"):
                return (t["start"] + t["end"]) / 2
        scenes = [t for t in timeline if t.get("kind") == "scene"]
        if len(scenes) > 1:
            return scenes[1]["start"] + 0.4 * (scenes[1]["end"] - scenes[1]["start"])
        return min(1.5, (render.duration_s or 2) / 2)

    # ------------------------------------------------------------------ checks

    def _publisher(self, publisher_id: str) -> Publisher:
        pub = self.publishers.get(publisher_id)
        if pub is None:
            raise PublishServiceError(f"Không có trình đăng '{publisher_id}'", 422)
        return pub

    def _account(self, s: Session, account_id: str) -> SocialAccount:
        acc = s.get(SocialAccount, account_id)
        if acc is None:
            raise PublishServiceError("Không tìm thấy tài khoản", 404)
        return acc

    def _candidates(self, s: Session, row: StoryboardRow) -> list[Render]:
        if not row.approved_hash:
            return []
        rows = s.scalars(select(Render).where(Render.storyboard_id == row.id,
                                              Render.status == "done")
                         .order_by(Render.created_at.desc()))
        out = []
        for r in rows:
            st = r.settings or {}
            if st.get("preview") or st.get("content_hash") != row.approved_hash:
                continue
            if not (self.settings.data_dir / (r.outputs or {}).get("video", "")).is_file():
                continue
            out.append(r)
        return out

    def pick_render(self, s: Session, row: StoryboardRow, publisher: Publisher,
                    options: dict[str, Any], render_id: str | None = None
                    ) -> tuple[Render | None, list[str]]:
        cands = self._candidates(s, row)
        aspects = sorted({r.aspect for r in cands})
        if render_id:
            r = s.get(Render, render_id)
            if r is None or r.storyboard_id != row.id:
                raise PublishServiceError("Không tìm thấy bản dựng", 404)
            return r, aspects
        c = publisher.constraints(options)
        order = ([c.preferred_aspect] if c.preferred_aspect else []) + c.aspect_ratios
        for aspect in order:
            for r in cands:
                if r.aspect == aspect:
                    return r, aspects
        approved = s.get(Render, row.approved_render_id) if row.approved_render_id else None
        return approved or (cands[0] if cands else None), aspects

    def _youtube_units_today(self, s: Session) -> int:
        row = s.get(AppSetting, "youtube_quota")
        today = datetime.now(UTC).astimezone(PACIFIC).date().isoformat()
        if row is None or row.value.get("day") != today:
            return 0
        return int(row.value.get("units", 0))

    def _record_quota(self, units: int) -> None:
        today = datetime.now(UTC).astimezone(PACIFIC).date().isoformat()
        with self.sf.begin() as s:
            row = s.get(AppSetting, "youtube_quota")
            value = {"day": today, "units": units}
            if row is None:
                s.add(AppSetting(key="youtube_quota", value=value))
            else:
                if row.value.get("day") == today:
                    value["units"] += int(row.value.get("units", 0))
                row.value = value

    def check(self, video_id: str, account_id: str, *, options: dict[str, Any] | None = None,
              schedule: dict[str, Any] | None = None, render_id: str | None = None,
              s: Session | None = None) -> dict[str, Any]:
        own = s is None
        s = s or self.sf()
        try:
            row = s.get(StoryboardRow, video_id)
            if row is None:
                raise PublishServiceError("Không tìm thấy video", 404)
            acc = self._account(s, account_id)
            publisher = self._publisher(acc.publisher_id)
            meta = self.resolved_meta(row, acc.platform, publisher, options)
            render, aspects = self.pick_render(s, row, publisher, meta["options"], render_id)
            issues: list[dict[str, Any]] = []
            if row.status not in lifecycle.PUBLISH_STATES or not row.approved_hash:
                issues.append({"level": "error", "code": "not_approved",
                               "message": "Video chưa được duyệt. Duyệt video trước khi đăng."})
            if render is None:
                issues.append({"level": "error", "code": "no_render",
                               "message": "Chưa có bản dựng đã duyệt."})
            elif (render.settings or {}).get("content_hash") != row.approved_hash:
                issues.append({"level": "error", "code": "render_not_approved",
                               "message": "Bản dựng này khác nội dung đã duyệt."})
            if acc.status == "expired":
                issues.append({"level": "error", "code": "account_expired",
                               "message": f"Tài khoản {acc.name} cần kết nối lại."})
            c = publisher.constraints(meta["options"])
            if render is not None:
                video = {"aspect": render.aspect, "duration_s": render.duration_s or 0,
                         "size_bytes": render.size_bytes or 0, "container": "mp4",
                         "codec": "h264", "has_captions": bool((render.outputs or {})
                                                               .get("srt"))}
                issues += validate(c, video=video, meta=meta, schedule=schedule,
                                   other_aspects=aspects)
            if acc.platform == "youtube" and not publisher.mock:
                used = self._youtube_units_today(s)
                if used + YOUTUBE_UPLOAD_COST > YOUTUBE_DAILY_QUOTA:
                    issues.append({"level": "warning", "code": "quota",
                                   "message": f"Hôm nay đã dùng ≈{used} / 10.000 đơn vị hạn mức "
                                              "YouTube; lần tải này có thể bị từ chối."})
                issues.append({"level": "info", "code": "unverified_private",
                               "message": "Nếu dự án Google Cloud chưa được xác minh, YouTube "
                                          "sẽ khoá video ở chế độ Riêng tư."})
                if render is not None and (render.duration_s or 0) > 15 * 60 and \
                        (acc.meta or {}).get("long_uploads") != "allowed":
                    issues.append({"level": "error", "code": "long_uploads",
                                   "message": "Kênh chưa xác minh chỉ đăng được video ≤ 15 "
                                              "phút (xác minh tại youtube.com/verify)."})
            dup = s.scalars(select(Publication).where(
                Publication.storyboard_id == video_id, Publication.account_id == account_id,
                Publication.status.in_(NOT_REPOSTABLE))).first()
            if dup is not None:
                issues.append({"level": "warning", "code": "duplicate",
                               "message": f"Video này đã có bài đăng lên {acc.name} "
                                          f"({dup.status}). Đăng lại sẽ tạo bài mới.",
                               "publication_id": dup.id})
            return {"account": self.social.account_out(acc),
                    "publisher": {"id": publisher.id, "name": publisher.name,
                                  "mock": publisher.mock},
                    "render": None if render is None else {
                        "id": render.id, "aspect": render.aspect,
                        "duration_s": render.duration_s, "size_bytes": render.size_bytes},
                    "available_aspects": aspects, "meta": meta,
                    "constraints": c.model_dump(), "issues": issues,
                    "ok": not has_errors(issues)}
        finally:
            if own:
                s.close()

    # ------------------------------------------------------------------ create

    def create(self, video_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """All-or-nothing: every item is validated before any publication is created."""
        if not items:
            raise PublishServiceError("Chọn ít nhất một tài khoản", 422)
        with self.sf.begin() as s:
            row = s.get(StoryboardRow, video_id)
            if row is None:
                raise PublishServiceError("Không tìm thấy video", 404)
            prepared = []
            problems: dict[str, Any] = {}
            for item in items:
                schedule = self._schedule(item.get("schedule"))
                result = self.check(video_id, item["account_id"],
                                    options=item.get("options"), schedule=schedule,
                                    render_id=item.get("render_id"), s=s)
                errors = [i for i in result["issues"] if i["level"] == "error"]
                if any(i["code"] == "duplicate" for i in result["issues"]) and \
                        not item.get("allow_repost"):
                    errors.append({"level": "error", "code": "duplicate",
                                   "message": "Đã đăng / đang đăng video này lên tài khoản "
                                              "này. Bật 'Đăng lại' nếu bạn muốn một bài mới."})
                if errors:
                    problems[item["account_id"]] = errors
                prepared.append((item, schedule, result))
            if problems:
                first = next(iter(problems.values()))[0]["message"]
                raise PublishServiceError(first, 422, problems)
            created = []
            for item, schedule, result in prepared:
                acc = self._account(s, item["account_id"])
                render = s.get(Render, result["render"]["id"])
                assert render is not None
                thumb = self.thumbnail_for(row, render) if result["constraints"][
                    "supports_thumbnail"] else None
                captions = {}
                if result["constraints"]["supports_captions"] and (render.outputs or {}).get(
                        "srt"):
                    captions[(row.data or {}).get("language") or "vi"] = render.outputs["srt"]
                meta = {**result["meta"],
                        "thumbnail": thumb.relative_to(self.settings.data_dir).as_posix()
                        if thumb else None,
                        "captions": captions, "aspect": render.aspect}
                mode = schedule["mode"]
                pub = Publication(
                    storyboard_id=video_id, render_id=render.id, account_id=acc.id,
                    publisher_id=acc.publisher_id, platform=acc.platform, meta=meta,
                    schedule_mode=mode, scheduled_at=schedule.get("at"),
                    status="scheduled" if mode == "local" else "queued",
                    warnings=[i["message"] for i in result["issues"]
                              if i["level"] == "warning"], upload_state={})
                s.add(pub)
                s.flush()
                lifecycle.log_event(
                    s, row, "publication_created", actor="user",
                    note=f"{acc.name}: " + (
                        "đăng ngay" if mode == "now" else
                        f"hẹn {schedule['at'].astimezone().strftime('%H:%M %d/%m/%Y')}"
                        + (" (trên nền tảng)" if mode == "platform" else "")),
                    render_id=render.id, data={"publication_id": pub.id,
                                               "platform": acc.platform})
                created.append(pub.id)
            lifecycle.sync_publications(s, row)
        for pid in created:
            self._enqueue_if_queued(pid)
        self._emit_video(video_id)
        return [self.get(pid) for pid in created]

    @staticmethod
    def _schedule(raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = raw or {}
        mode = raw.get("mode") or "now"
        if mode not in ("now", "local", "platform"):
            raise PublishServiceError("Kiểu hẹn giờ không hợp lệ", 422)
        at = raw.get("at")
        if isinstance(at, str):
            at = datetime.fromisoformat(at.replace("Z", "+00:00"))
        return {"mode": mode, "at": _aware(at) if mode != "now" else None}

    def _enqueue_if_queued(self, pub_id: str) -> None:
        with self.sf.begin() as s:
            pub = s.get(Publication, pub_id)
            if pub is None or pub.status != "queued":
                return
            row = s.get(StoryboardRow, pub.storyboard_id)
            project_id = row.project_id if row else None
        job = self.queue.enqueue("publish", {"publication_id": pub_id, "steps": PUBLISH_STEPS},
                                 project_id=project_id)
        with self.sf.begin() as s:
            pub = s.get(Publication, pub_id)
            if pub is not None:
                pub.job_id = job["id"]

    # ------------------------------------------------------------------ read

    def public_url(self, url: str | None) -> str | None:
        """Mock post URLs are stored without the media key (it changes every launch)."""
        if url and url.startswith(MOCK_POST_PREFIX) and self.mock_url:
            return self.mock_url(url[len(MOCK_POST_PREFIX):].split("?")[0])
        return url

    def pub_out(self, p: Publication, account: SocialAccount | None = None,
                title: str | None = None) -> dict[str, Any]:
        return {"id": p.id, "storyboard_id": p.storyboard_id, "video_title": title,
                "render_id": p.render_id, "account_id": p.account_id,
                "account_name": account.name if account else None,
                "publisher_id": p.publisher_id, "platform": p.platform,
                "mock": p.publisher_id.startswith("mock_"), "status": p.status,
                "meta": {k: v for k, v in (p.meta or {}).items()},
                "schedule_mode": p.schedule_mode, "scheduled_at": p.scheduled_at,
                "next_attempt_at": p.next_attempt_at, "attempts": p.attempts,
                "progress": p.progress, "remote_id": p.remote_id,
                "remote_url": self.public_url(p.remote_url),
                "warnings": p.warnings or [], "job_id": p.job_id, "error": p.error,
                "created_at": p.created_at, "updated_at": p.updated_at,
                "published_at": p.published_at,
                "resumable": bool((p.upload_state or {}).get("offset"))}

    def get(self, pub_id: str) -> dict[str, Any]:
        with self.sf() as s:
            p = s.get(Publication, pub_id)
            if p is None:
                raise PublishServiceError("Không tìm thấy bài đăng", 404)
            acc = s.get(SocialAccount, p.account_id) if p.account_id else None
            row = s.get(StoryboardRow, p.storyboard_id)
            return self.pub_out(p, acc, row.title if row else None)

    def list(self, video_id: str | None = None, status: str | None = None
             ) -> list[dict[str, Any]]:
        with self.sf() as s:
            stmt = select(Publication).order_by(Publication.created_at.desc())
            if video_id:
                stmt = stmt.where(Publication.storyboard_id == video_id)
            if status:
                stmt = stmt.where(Publication.status.in_(status.split(",")))
            out = []
            accounts = {a.id: a for a in s.scalars(select(SocialAccount))}
            titles = dict(s.execute(select(StoryboardRow.id, StoryboardRow.title)).all())
            for p in s.scalars(stmt):
                out.append(self.pub_out(p, accounts.get(p.account_id or ""),
                                        titles.get(p.storyboard_id)))
            return out

    def _emit(self, pub_id: str) -> None:
        try:
            self.bus.publish({"type": "publication", **_jsonable(self.get(pub_id))})
        except PublishServiceError:
            pass

    def _emit_video(self, video_id: str) -> None:
        with self.sf() as s:
            row = s.get(StoryboardRow, video_id)
            if row is not None:
                self.bus.publish({"type": "video", "id": video_id, "status": row.status})

    # ------------------------------------------------------------------ job

    def _update(self, pub_id: str, sync: bool = True, note: str = "",
                **fields: Any) -> Publication | None:
        with self.sf.begin() as s:
            pub = s.get(Publication, pub_id)
            if pub is None:
                return None
            for k, v in fields.items():
                setattr(pub, k, v)
            if sync:
                row = s.get(StoryboardRow, pub.storyboard_id)
                if row is not None:
                    lifecycle.sync_publications(s, row, note=note)
            s.flush()
            s.expunge(pub)
        self._emit(pub_id)
        if sync:
            self._emit_video(pub.storyboard_id)
        return pub

    def _save_state(self, pub_id: str, state: dict[str, Any]) -> None:
        with self.sf.begin() as s:
            pub = s.get(Publication, pub_id)
            if pub is None:
                return
            pub.upload_state = state
            remote = state.get("video_id") or state.get("remote_id")
            if remote and not pub.remote_id:
                pub.remote_id = str(remote)

    def _request(self, pub: Publication) -> PublishRequest:
        acc_ref, _ = self.social.account_ref(pub.account_id or "")
        with self.sf() as s:
            render = s.get(Render, pub.render_id) if pub.render_id else None
            if render is None or not render.outputs.get("video"):
                raise PublishError("Bản dựng đã bị xoá; không thể đăng.")
            video = self.settings.data_dir / render.outputs["video"]
            if not video.is_file():
                raise PublishError(f"Không tìm thấy tệp video {video.name}")
            meta = pub.meta or {}
            thumb = self.settings.data_dir / meta["thumbnail"] if meta.get("thumbnail") \
                else None
            captions = {lang: self.settings.data_dir / rel
                        for lang, rel in (meta.get("captions") or {}).items()
                        if (self.settings.data_dir / rel).is_file()}
            st = render.settings or {}
            return PublishRequest(
                publication_id=pub.id, account=acc_ref, video_path=video,
                duration_s=render.duration_s or 0, width=st.get("width", 0),
                height=st.get("height", 0), aspect=render.aspect,
                size_bytes=video.stat().st_size, title=meta.get("title", ""),
                description=meta.get("description", ""), tags=meta.get("tags", []),
                thumbnail_path=thumb if thumb and thumb.is_file() else None,
                captions=captions,
                scheduled_at=pub.scheduled_at if pub.schedule_mode == "platform" else None,
                privacy=(meta.get("options") or {}).get("privacy", "public"),
                options=meta.get("options") or {})

    def _pctx(self, pub: Publication, ctx: JobContext | None) -> PublishContext:
        last = {"pct": -1}

        def progress(pct: float, msg: str | None) -> None:
            if ctx is not None:
                ctx.report_progress(pct, msg)
            if int(pct) != last["pct"]:
                last["pct"] = int(pct)
                with self.sf.begin() as s:
                    p = s.get(Publication, pub.id)
                    if p is not None:
                        p.progress = int(pct)
                self.bus.publish({"type": "publication.progress", "id": pub.id,
                                  "progress": int(pct), "message": msg})

        account_id = pub.account_id or ""
        return PublishContext(
            state=dict(pub.upload_state or {}),
            _save=lambda st: self._save_state(pub.id, st),
            _token=lambda force: self.social.access_token(account_id, force),
            _progress=progress,
            _log=(ctx.log if ctx else (lambda m, level="info": log.info(m))),
            _cancelled=(ctx.is_cancelled if ctx else (lambda: False)),
            data_dir=self.settings.data_dir, sleep=self.sleep,
            extras={})

    async def run(self, pub_id: str, ctx: JobContext) -> dict[str, Any]:
        with self.sf.begin() as s:
            pub = s.get(Publication, pub_id)
            if pub is None:
                return {"skipped": "missing"}
            if pub.status in ("published", "scheduled_remote", "processing", "cancelled",
                              "failed", "missed"):
                ctx.log(f"Bỏ qua: bài đăng đang ở trạng thái {pub.status} (không đăng lại)")
                return {"skipped": pub.status, "remote_url": pub.remote_url}
            if pub.status == "scheduled" and pub.schedule_mode == "local":
                return {"skipped": "not_due"}
            pub.status, pub.error, pub.job_id = "uploading", None, ctx.job_id
            pub.attempts += 1
            row = s.get(StoryboardRow, pub.storyboard_id)
            if row is not None:
                lifecycle.sync_publications(s, row)
            s.flush()
            s.expunge(pub)
            attempts = pub.attempts
        self._emit(pub_id)
        self._emit_video(pub.storyboard_id)
        publisher = self.publishers.get(pub.publisher_id)
        try:
            if publisher is None:
                raise PublishError(f"Không có trình đăng '{pub.publisher_id}'")
            req = self._request(pub)
            pctx = self._pctx(pub, ctx)
            ctx.log(f"Đăng lên {publisher.name} → {req.account.name} (lần {attempts})")
            with keep_awake(f"publish {pub_id}"):
                result = await publisher.publish(req, pctx)
        except JobCancelled:
            current = self._status(pub_id)
            if current != "cancelled":
                self._update(pub_id, status="queued", note="Tạm dừng do tắt ứng dụng")
            raise
        except PublishError as exc:
            return self._failed(pub_id, exc, attempts, ctx)
        except Exception as exc:  # noqa: BLE001
            log.exception("Publish %s crashed", pub_id)
            err = PublishError(f"Lỗi không mong muốn: {type(exc).__name__}: {exc}",
                               retryable=True)
            return self._failed(pub_id, err, attempts, ctx)
        if publisher.platform == "youtube" and not publisher.mock and \
                not pctx.state.get("quota_counted"):
            units = YOUTUBE_UPLOAD_COST + (50 if req.thumbnail_path else 0) + \
                400 * len(req.captions)
            self._record_quota(units)
            pctx.save_state(quota_counted=True)
        self._apply_result(pub_id, result)
        return {"publication_id": pub_id, "status": self._status(pub_id),
                "remote_id": result.remote_id, "url": result.url}

    def _status(self, pub_id: str) -> str | None:
        with self.sf() as s:
            p = s.get(Publication, pub_id)
            return p.status if p else None

    def _failed(self, pub_id: str, exc: PublishError, attempts: int,
                ctx: JobContext | None) -> dict[str, Any]:
        now = utcnow()
        if ctx is not None:
            ctx.log(exc.message, "warning" if exc.retryable else "error")
        with self.sf() as s:
            pub = s.get(Publication, pub_id)
            title = (pub.meta or {}).get("title", "") if pub else ""
            acc = s.get(SocialAccount, pub.account_id) if pub and pub.account_id else None
            acc_name = acc.name if acc else ""
        if exc.code == "quota":
            when = next_quota_reset(now) if getattr(exc, "daily", False) else \
                now + timedelta(seconds=backoff_s(attempts))
            self._update(pub_id, status="retry_wait", error=exc.message, next_attempt_at=when,
                         note=exc.message)
            self.notify(level="warning", title=f"Tạm hoãn đăng “{title}”",
                        message=f"{exc.message} Sẽ thử lại lúc "
                                f"{when.astimezone().strftime('%H:%M %d/%m')}.",
                        link="/videos")
            return {"status": "retry_wait", "error": exc.message}
        if exc.retryable and attempts < MAX_ATTEMPTS:
            when = now + timedelta(seconds=backoff_s(attempts))
            self._update(pub_id, status="retry_wait", error=exc.message, next_attempt_at=when)
            if ctx is not None:
                ctx.log(f"Sẽ thử lại sau {backoff_s(attempts)} giây (lần {attempts + 1}/"
                        f"{MAX_ATTEMPTS}), tiếp tục từ chỗ đã tải", "warning")
            return {"status": "retry_wait", "error": exc.message,
                    "next_attempt_at": when.isoformat()}
        self._update(pub_id, status="failed", error=exc.message, next_attempt_at=None,
                     note=exc.message)
        self.notify(level="error", title=f"Đăng thất bại: {title}",
                    message=f"{acc_name}: {exc.message}", link="/videos")
        raise exc

    def _apply_result(self, pub_id: str, result: PublishResult) -> None:
        now = utcnow()
        fields: dict[str, Any] = {"remote_id": result.remote_id, "error": None}
        if result.url:
            fields["remote_url"] = result.url
        with self.sf() as s:
            pub = s.get(Publication, pub_id)
            if pub is None:
                return
            warnings = list(pub.warnings or [])
            scheduled_at = pub.scheduled_at
            title = (pub.meta or {}).get("title", "")
            acc = s.get(SocialAccount, pub.account_id) if pub.account_id else None
            acc_name = acc.name if acc else ""
        for w in result.warnings:
            if w not in warnings:
                warnings.append(w)
        fields["warnings"] = warnings
        if result.state == "published":
            fields.update(status="published", published_at=result.published_at or now,
                          progress=100, next_attempt_at=None)
            self._update(pub_id, **fields)
            self.notify(level="success", title=f"Đã đăng “{title}”",
                        message=f"{acc_name}: {self.public_url(result.url) or ''}",
                        link=self.public_url(result.url), data={"publication_id": pub_id})
        elif result.state == "scheduled":
            poll = (scheduled_at or now) + timedelta(seconds=60)
            fields.update(status="scheduled_remote", progress=100, next_attempt_at=poll)
            self._update(pub_id, **fields)
            self.notify(level="success", title=f"Đã lên lịch “{title}”",
                        message=f"{acc_name} sẽ đăng lúc "
                                f"{(scheduled_at or now).astimezone().strftime('%H:%M %d/%m')}",
                        link=self.public_url(result.url))
        elif result.state == "processing":
            fields.update(status="processing", progress=100,
                          next_attempt_at=now + timedelta(seconds=POLL_PROCESSING_S))
            self._update(pub_id, **fields)
        else:
            fields.update(status="failed", error=result.detail or "Nền tảng từ chối video",
                          next_attempt_at=None)
            self._update(pub_id, **fields)
            self.notify(level="error", title=f"Đăng thất bại: {title}",
                        message=f"{acc_name}: {result.detail or ''}")

    # ------------------------------------------------------------------ status polling

    async def poll(self, pub_id: str) -> str | None:
        with self.sf() as s:
            pub = s.get(Publication, pub_id)
            if pub is None or pub.status not in ("processing", "scheduled_remote") \
                    or not pub.remote_id:
                return None
            s.expunge(pub)
        publisher = self.publishers.get(pub.publisher_id)
        if publisher is None:
            return None
        pctx = self._pctx(pub, None)
        now = utcnow()
        try:
            result = await publisher.status(pub.remote_id, pctx)
        except PublishError as exc:
            if exc.code == "auth":
                self._update(pub_id, status=pub.status, error=exc.message,
                             next_attempt_at=None)
                return pub.status
            self._update(pub_id, sync=False,
                         next_attempt_at=now + timedelta(seconds=backoff_s(3)))
            return pub.status
        if result.state == "processing":
            created = _aware(pub.updated_at) or now
            if now - created > POLL_MAX_AGE:
                self._update(pub_id, next_attempt_at=None,
                             warnings=[*(pub.warnings or []),
                                       "Nền tảng xử lý quá lâu; kiểm tra trực tiếp."])
            else:
                self._update(pub_id, sync=False,
                             next_attempt_at=now + timedelta(seconds=60))
            return "processing"
        if result.state == "scheduled":
            at = _aware(pub.scheduled_at) or now
            self._update(pub_id, sync=False,
                         next_attempt_at=max(now + timedelta(seconds=60),
                                             at + timedelta(seconds=60)))
            return "scheduled_remote"
        if result.url is None:
            result.url = pub.remote_url
        self._apply_result(pub_id, result)
        return self._status(pub_id)

    # ------------------------------------------------------------------ user actions

    def cancel(self, pub_id: str) -> dict[str, Any]:
        with self.sf() as s:
            pub = s.get(Publication, pub_id)
            if pub is None:
                raise PublishServiceError("Không tìm thấy bài đăng", 404)
            status, job_id = pub.status, pub.job_id
        if status in ("published",):
            raise PublishServiceError("Bài đã đăng; hãy xoá trực tiếp trên nền tảng", 409)
        if status in ("scheduled_remote", "processing"):
            raise PublishServiceError("Video đã được gửi lên nền tảng. Huỷ lịch hoặc xoá "
                                      "trong YouTube Studio / Meta Business Suite.", 409)
        if status == "cancelled":
            return self.get(pub_id)
        self._update(pub_id, status="cancelled", next_attempt_at=None,
                     note="Huỷ bài đăng")
        if status == "uploading" and job_id:
            try:
                self.queue.cancel(job_id)
            except Exception:  # noqa: BLE001 - already finished
                pass
        return self.get(pub_id)

    def retry(self, pub_id: str) -> dict[str, Any]:
        with self.sf.begin() as s:
            pub = s.get(Publication, pub_id)
            if pub is None:
                raise PublishServiceError("Không tìm thấy bài đăng", 404)
            if pub.status not in ("failed", "retry_wait", "missed"):
                raise PublishServiceError("Chỉ thử lại được bài lỗi / quá hạn", 409)
            pub.status, pub.next_attempt_at, pub.attempts = "queued", None, 0
            if pub.schedule_mode == "platform" and pub.scheduled_at and \
                    _aware(pub.scheduled_at) < utcnow() + timedelta(minutes=11):
                pub.schedule_mode, pub.scheduled_at = "now", None
            row = s.get(StoryboardRow, pub.storyboard_id)
            if row is not None:
                lifecycle.sync_publications(s, row)
        self._enqueue_if_queued(pub_id)
        self._emit(pub_id)
        return self.get(pub_id)

    def reschedule(self, pub_id: str, at: datetime) -> dict[str, Any]:
        at = _aware(at)  # type: ignore[assignment]
        if at is None or at <= utcnow() + timedelta(seconds=30):
            raise PublishServiceError("Thời gian mới phải ở tương lai", 422)
        with self.sf.begin() as s:
            pub = s.get(Publication, pub_id)
            if pub is None:
                raise PublishServiceError("Không tìm thấy bài đăng", 404)
            if pub.status not in ("scheduled", "missed", "retry_wait", "failed", "queued"):
                raise PublishServiceError("Không đổi lịch được ở trạng thái này", 409)
            pub.status, pub.scheduled_at, pub.next_attempt_at = "scheduled", at, None
            pub.schedule_mode = "local"
            row = s.get(StoryboardRow, pub.storyboard_id)
            if row is not None:
                lifecycle.sync_publications(s, row, note="Đổi lịch đăng")
        self._emit(pub_id)
        return self.get(pub_id)

    # ------------------------------------------------------------------ scheduler hooks

    def requeue_interrupted(self) -> int:
        """At startup: publications left 'uploading' by a crash continue (resume state is
        kept). The job queue has already re-queued their jobs if they were running."""
        ids = []
        with self.sf.begin() as s:
            for pub in s.scalars(select(Publication).where(Publication.status == "uploading")):
                pub.status = "queued"
                job = self.queue.get(pub.job_id, include_logs=False) if pub.job_id else None
                if job is None or job["status"] != "queued":
                    ids.append(pub.id)
        for pid in ids:
            self._enqueue_if_queued(pid)
        return len(ids)

    def due(self, now: datetime | None = None) -> list[str]:
        """Local schedules and retries whose time has come → queued + job."""
        now = now or utcnow()
        ids = []
        with self.sf.begin() as s:
            rows = s.scalars(select(Publication).where(
                ((Publication.status == "scheduled") & (Publication.schedule_mode == "local")
                 & (Publication.scheduled_at <= now))
                | ((Publication.status == "retry_wait") & (Publication.next_attempt_at <= now))))
            for pub in rows:
                pub.status = "queued"
                ids.append(pub.id)
        for pid in ids:
            self._enqueue_if_queued(pid)
            self._emit(pid)
        return ids

    def to_poll(self, now: datetime | None = None) -> list[str]:
        now = now or utcnow()
        with self.sf() as s:
            return list(s.scalars(select(Publication.id).where(
                Publication.status.in_(("processing", "scheduled_remote")),
                Publication.next_attempt_at.is_not(None),
                Publication.next_attempt_at <= now)))

    def detect_missed(self, now: datetime | None = None,
                      grace_s: int = MISSED_GRACE_S) -> list[dict[str, Any]]:
        """At startup: local schedules that passed while the app was not running."""
        now = now or utcnow()
        limit = now - timedelta(seconds=grace_s)
        ids = []
        with self.sf.begin() as s:
            for pub in s.scalars(select(Publication).where(
                    Publication.status == "scheduled", Publication.schedule_mode == "local",
                    Publication.scheduled_at < limit)):
                pub.status = "missed"
                ids.append(pub.id)
                row = s.get(StoryboardRow, pub.storyboard_id)
                if row is not None:
                    lifecycle.log_event(s, row, "publication_missed", actor="system",
                                        note="Lỡ giờ đăng (ứng dụng không chạy)",
                                        data={"publication_id": pub.id})
        if ids:
            self.notify(level="warning", title=f"{len(ids)} bài đăng bị lỡ giờ",
                        message="Ứng dụng không chạy vào giờ đã hẹn. Chọn đăng ngay, đổi lịch "
                                "hoặc bỏ qua.", link="/videos?missed=1",
                        data={"missed": ids})
        return self.missed()

    def missed(self) -> list[dict[str, Any]]:
        return self.list(status="missed")

    def resolve_missed(self, ids: list[str], action: str, at: datetime | None = None
                       ) -> list[dict[str, Any]]:
        if action not in ("publish_now", "reschedule", "skip"):
            raise PublishServiceError("Thao tác không hợp lệ", 422)
        out = []
        for pid in ids:
            with self.sf() as s:
                pub = s.get(Publication, pid)
                if pub is None or pub.status != "missed":
                    continue
            if action == "publish_now":
                with self.sf.begin() as s:
                    pub = s.get(Publication, pid)
                    pub.status, pub.schedule_mode = "queued", "now"  # type: ignore[union-attr]
                self._enqueue_if_queued(pid)
                self._update(pid, note="Đăng ngay bài bị lỡ giờ")
            elif action == "reschedule":
                if at is None:
                    raise PublishServiceError("Chọn thời gian mới", 422)
                self.reschedule(pid, at)
            else:
                self._update(pid, status="cancelled", note="Bỏ qua bài bị lỡ giờ")
            out.append(self.get(pid))
        return out

    def next_local_schedule(self) -> datetime | None:
        with self.sf() as s:
            return _aware(s.scalars(select(Publication.scheduled_at).where(
                Publication.status == "scheduled", Publication.schedule_mode == "local")
                .order_by(Publication.scheduled_at)).first())

    def busy(self) -> bool:
        with self.sf() as s:
            return s.scalars(select(Publication.id).where(
                Publication.status.in_(("queued", "uploading")))).first() is not None


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out
