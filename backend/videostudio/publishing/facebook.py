"""Facebook Page publisher: chunked (resumable) page video upload and Reels."""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .base import (
    PublishConstraints,
    PublishContext,
    Publisher,
    PublishError,
    PublishRequest,
    PublishResult,
    http_client,
    request,
    safe_title,
)
from .oauth import FB_GRAPH, FB_VERSION, facebook_error

GRAPH_VIDEO = f"https://graph-video.facebook.com/{FB_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{FB_VERSION}"
REEL_CHUNK = 8 * 1024 * 1024
MAX_RETRIES = 6
SCHEDULE_MIN_S = 10 * 60
SCHEDULE_MAX_S = 75 * 86400


def page_video_url(page_id: str, video_id: str) -> str:
    return f"https://www.facebook.com/{page_id}/videos/{video_id}"


def reel_url(video_id: str) -> str:
    return f"https://www.facebook.com/reel/{video_id}"


class FacebookPagePublisher(Publisher):
    id = "facebook_page"
    name = "Trang Facebook"
    platform = "facebook"
    description = "Đăng video hoặc Reels lên Trang Facebook bạn quản trị (Graph API)."

    def constraints(self, options: dict[str, Any] | None = None) -> PublishConstraints:
        fmt = (options or {}).get("format", "video")
        common = dict(platform="Facebook", supports_scheduling=True,
                      min_schedule_ahead_s=SCHEDULE_MIN_S, max_schedule_ahead_s=SCHEDULE_MAX_S,
                      formats=["mp4", "mov"], video_codecs=["h264", "hevc"],
                      max_description_len=60_000, max_hashtags=30)
        if fmt == "reels":
            return PublishConstraints(
                **common, format="reels", aspect_ratios=["9:16"], preferred_aspect="9:16",
                min_duration_s=3, max_duration_s=90, max_file_size_mb=1024,
                supports_captions=False, supports_thumbnail=False,
                notes=["Reels: video dọc 9:16, 3–90 giây, khuyến nghị 1080×1920.",
                       "Reels không nhận tệp phụ đề qua API; nên bật phụ đề in sẵn."])
        return PublishConstraints(
            **common, format="video", aspect_ratios=["16:9", "9:16", "1:1"],
            min_duration_s=1, max_duration_s=240 * 60, max_file_size_mb=10_000,
            max_title_len=255, supports_captions=True, caption_formats=["srt"],
            supports_thumbnail=True,
            notes=["Hẹn giờ trên Facebook: từ 10 phút đến 75 ngày kể từ bây giờ.",
                   "Ứng dụng Meta ở chế độ Development chỉ đăng được lên Trang mà chính "
                   "bạn quản trị — đủ cho mục đích cá nhân."])

    def metadata_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "format": {"type": "string", "title": "Kiểu bài đăng", "default": "video",
                       "enum": ["video", "reels"], "x-enumLabels": ["Video trên Trang", "Reels"]},
            "upload_captions": {"type": "boolean", "title": "Tải phụ đề SRT lên (video)",
                                "default": True},
        }}

    # ------------------------------------------------------------------ publish

    async def publish(self, req: PublishRequest, pctx: PublishContext) -> PublishResult:
        fmt = {**self.default_options(), **req.options}.get("format", "video")
        if fmt == "reels":
            return await self._publish_reel(req, pctx)
        return await self._publish_video(req, pctx)

    async def _post(self, http: httpx.AsyncClient, url: str, label: str, pctx: PublishContext,
                    *, data: dict[str, Any] | None = None, files: Any = None,
                    retries: int = MAX_RETRIES) -> dict[str, Any]:
        attempt = 0
        while True:
            token = await pctx.access_token()
            try:
                resp = await request(http, "POST", url, label=label,
                                     data={**(data or {}), "access_token": token}, files=files)
            except PublishError as exc:
                error = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                error = facebook_error(resp, label)
            if not error.retryable or attempt >= retries:
                raise error
            attempt += 1
            delay = min(60.0, 2 ** attempt)
            pctx.log(f"{error.message} — thử lại sau {delay:.0f}s", "warning")
            await pctx.sleep(delay)

    def _schedule_fields(self, req: PublishRequest) -> dict[str, Any]:
        if not req.scheduled_at:
            return {"published": "true"}
        return {"published": "false",
                "scheduled_publish_time": str(int(req.scheduled_at.timestamp()))}

    async def _publish_video(self, req: PublishRequest, pctx: PublishContext) -> PublishResult:
        state = pctx.state
        page = req.account.external_id
        warnings: list[str] = list(state.get("warnings", []))
        path = req.video_path
        total = path.stat().st_size
        url = f"{GRAPH_VIDEO}/{page}/videos"
        async with http_client(300) as http:
            if not state.get("finished"):
                if not state.get("upload_session_id") or state.get("total") != total:
                    start = await self._post(http, url, "Facebook bắt đầu tải lên", pctx,
                                             data={"upload_phase": "start",
                                                   "file_size": str(total)})
                    pctx.save_state(video_id=start["video_id"],
                                    upload_session_id=start["upload_session_id"],
                                    start_offset=int(start["start_offset"]),
                                    end_offset=int(start["end_offset"]), total=total)
                    pctx.log(f"Phiên tải lên Facebook: video {start['video_id']}")
                else:
                    pctx.log(f"Tiếp tục tải lên Facebook từ byte {state['start_offset']}")
                with path.open("rb") as fh:
                    while state["start_offset"] < state["end_offset"]:
                        pctx.check_cancelled()
                        fh.seek(state["start_offset"])
                        chunk = fh.read(state["end_offset"] - state["start_offset"])
                        got = await self._post(
                            http, url, "Facebook tải lên", pctx,
                            data={"upload_phase": "transfer",
                                  "upload_session_id": state["upload_session_id"],
                                  "start_offset": str(state["start_offset"])},
                            files={"video_file_chunk": ("chunk", chunk,
                                                        "application/octet-stream")})
                        pctx.save_state(start_offset=int(got["start_offset"]),
                                        end_offset=int(got["end_offset"]))
                        pctx.progress(85 * state["start_offset"] / max(1, total),
                                      f"Đang tải lên Facebook "
                                      f"{state['start_offset'] * 100 // max(1, total)}%")
                fields = {"upload_phase": "finish",
                          "upload_session_id": state["upload_session_id"],
                          "title": safe_title(req.title, 255), "description": req.description,
                          **self._schedule_fields(req)}
                await self._post(http, url, "Facebook hoàn tất", pctx, data=fields)
                pctx.save_state(finished=True)
                pctx.progress(88, "Facebook đang xử lý video")
            video_id = state["video_id"]
            o = {**self.default_options(), **req.options}
            if o.get("upload_captions", True):
                done = dict(state.get("captions_done", {}))
                for lang, cap in req.captions.items():
                    if done.get(lang):
                        continue
                    warn = await self._caption(http, pctx, video_id, lang, cap)
                    if warn:
                        warnings.append(warn)
                    done[lang] = True
                    pctx.save_state(captions_done=done, warnings=warnings)
            if req.thumbnail_path and not state.get("thumbnail_done"):
                warn = await self._thumbnail(http, pctx, video_id, req.thumbnail_path)
                if warn:
                    warnings.append(warn)
                pctx.save_state(thumbnail_done=True, warnings=warnings)
        pctx.progress(100, "Đã gửi lên Facebook")
        link = page_video_url(page, video_id)
        if req.scheduled_at:
            return PublishResult(state="scheduled", remote_id=video_id, url=link,
                                 warnings=warnings,
                                 detail="Facebook sẽ tự đăng vào giờ đã hẹn.")
        return PublishResult(state="processing", remote_id=video_id, url=link,
                             warnings=warnings, detail="Facebook đang xử lý video.")

    async def _caption(self, http: httpx.AsyncClient, pctx: PublishContext, video_id: str,
                       lang: str, path: Path) -> str | None:
        locale = {"vi": "vi_VN", "en": "en_US"}.get(lang, lang)
        try:
            await self._post(http, f"{FB_GRAPH}/{video_id}/captions", "Facebook phụ đề", pctx,
                             data={"default_locale": locale},
                             files={"captions_file": (f"captions.{locale}.srt",
                                                      path.read_bytes(), "text/plain")},
                             retries=2)
        except PublishError as exc:
            return f"Không tải được phụ đề: {exc.message}"
        pctx.log(f"Đã tải phụ đề {locale}")
        return None

    async def _thumbnail(self, http: httpx.AsyncClient, pctx: PublishContext, video_id: str,
                         path: Path) -> str | None:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        try:
            await self._post(http, f"{FB_GRAPH}/{video_id}/thumbnails", "Facebook ảnh thu nhỏ",
                             pctx, data={"is_preferred": "true"},
                             files={"source": (path.name, path.read_bytes(), mime)}, retries=2)
        except PublishError as exc:
            return f"Không đặt được ảnh thu nhỏ: {exc.message}"
        pctx.log("Đã đặt ảnh thu nhỏ")
        return None

    async def _publish_reel(self, req: PublishRequest, pctx: PublishContext) -> PublishResult:
        state = pctx.state
        page = req.account.external_id
        path = req.video_path
        total = path.stat().st_size
        reels_url = f"{FB_GRAPH}/{page}/video_reels"
        async with http_client(300) as http:
            if not state.get("finished"):
                if not state.get("video_id") or state.get("total") != total:
                    start = await self._post(http, reels_url, "Facebook Reels bắt đầu", pctx,
                                             data={"upload_phase": "start"})
                    pctx.save_state(video_id=start["video_id"], offset=0, total=total,
                                    reel=True)
                video_id = state["video_id"]
                await self._rupload(http, pctx, video_id, path, total)
                fields: dict[str, Any] = {"upload_phase": "finish", "video_id": video_id,
                                          "description": req.description,
                                          "title": safe_title(req.title, 255)}
                if req.scheduled_at:
                    fields["video_state"] = "SCHEDULED"
                    fields["scheduled_publish_time"] = str(int(req.scheduled_at.timestamp()))
                else:
                    fields["video_state"] = "PUBLISHED"
                await self._post(http, reels_url, "Facebook Reels hoàn tất", pctx, data=fields)
                pctx.save_state(finished=True)
        video_id = state["video_id"]
        pctx.progress(100, "Đã gửi Reels lên Facebook")
        if req.scheduled_at:
            return PublishResult(state="scheduled", remote_id=video_id, url=reel_url(video_id),
                                 detail="Facebook sẽ tự đăng Reels vào giờ đã hẹn.")
        return PublishResult(state="processing", remote_id=video_id, url=reel_url(video_id),
                             detail="Facebook đang xử lý Reels.")

    async def _rupload(self, http: httpx.AsyncClient, pctx: PublishContext, video_id: str,
                       path: Path, total: int) -> None:
        url = f"{RUPLOAD}/{video_id}"
        state = pctx.state
        if state.get("offset"):
            # ask the upload server where to continue
            token = await pctx.access_token()
            resp = await request(http, "GET", url, label="Facebook Reels",
                                 headers={"Authorization": f"OAuth {token}"})
            if resp.status_code == 200:
                phase = (resp.json().get("video_status") or {}).get("uploading_phase") or {}
                if "bytes_transferred" in phase:
                    pctx.save_state(offset=int(phase["bytes_transferred"]))
        attempt = 0
        with path.open("rb") as fh:
            while state.get("offset", 0) < total:
                pctx.check_cancelled()
                offset = int(state.get("offset", 0))
                fh.seek(offset)
                chunk = fh.read(REEL_CHUNK)
                token = await pctx.access_token()
                try:
                    resp = await request(http, "POST", url, label="Facebook Reels",
                                         headers={"Authorization": f"OAuth {token}",
                                                  "offset": str(offset),
                                                  "file_size": str(total),
                                                  "Content-Type": "application/octet-stream"},
                                         content=chunk)
                except PublishError as exc:
                    error = exc
                else:
                    if resp.status_code == 200 and resp.json().get("success", True):
                        attempt = 0
                        pctx.save_state(offset=offset + len(chunk))
                        pctx.progress(90 * state["offset"] / max(1, total),
                                      f"Đang tải Reels {state['offset'] * 100 // total}%")
                        continue
                    error = facebook_error(resp, "Facebook Reels tải lên")
                if not error.retryable or attempt >= MAX_RETRIES:
                    raise error
                attempt += 1
                await pctx.sleep(min(60.0, 2 ** attempt))

    # ------------------------------------------------------------------ status

    async def status(self, remote_id: str, pctx: PublishContext) -> PublishResult:
        token = await pctx.access_token()
        async with http_client(30) as http:
            resp = await request(http, "GET", f"{FB_GRAPH}/{remote_id}", label="Facebook",
                                 params={"fields": "status,permalink_url,published,"
                                                   "scheduled_publish_time",
                                         "access_token": token})
        if resp.status_code != 200:
            raise facebook_error(resp, "Facebook trạng thái")
        data = resp.json()
        st = data.get("status") or {}
        if st.get("video_status") == "error":
            return PublishResult(state="failed", remote_id=remote_id,
                                 detail="Facebook báo lỗi xử lý video")
        permalink = data.get("permalink_url")
        url = f"https://www.facebook.com{permalink}" if permalink and permalink.startswith("/") \
            else permalink
        if data.get("published") or (st.get("video_status") == "ready"
                                     and not data.get("scheduled_publish_time")
                                     and data.get("published") is None):
            return PublishResult(state="published", remote_id=remote_id, url=url,
                                 published_at=datetime.now(UTC))
        if data.get("scheduled_publish_time"):
            return PublishResult(state="scheduled", remote_id=remote_id, url=url)
        return PublishResult(state="processing", remote_id=remote_id, url=url)
