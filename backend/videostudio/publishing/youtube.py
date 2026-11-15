"""YouTube Data API v3 publisher: resumable upload, thumbnail, SRT captions, playlist."""

from __future__ import annotations

import json
import mimetypes
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .base import (
    PublishAuthError,
    PublishConstraints,
    PublishContext,
    Publisher,
    PublishError,
    PublishQuotaError,
    PublishRequest,
    PublishResult,
    error_excerpt,
    http_client,
    request,
    safe_title,
)
from .oauth import YOUTUBE_API

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CAPTIONS_URL = "https://www.googleapis.com/upload/youtube/v3/captions"
CHUNK = 8 * 1024 * 1024  # multiple of 256 KiB, as the resumable protocol requires
MAX_CHUNK_RETRIES = 6
# Costs in quota units (default project quota: 10 000/day).
QUOTA_COST = {"upload": 1600, "thumbnail": 50, "captions": 400, "playlist": 50, "list": 1}
DAILY_QUOTA = 10_000

CATEGORIES = {"27": "Giáo dục", "28": "Khoa học & Công nghệ", "22": "Mọi người & Blog",
              "26": "Hướng dẫn & Phong cách", "24": "Giải trí", "25": "Tin tức & Chính trị",
              "10": "Âm nhạc", "20": "Trò chơi", "17": "Thể thao", "19": "Du lịch & Sự kiện",
              "1": "Phim & Hoạt hình", "2": "Ô tô & Xe cộ", "15": "Thú cưng & Động vật",
              "29": "Tổ chức phi lợi nhuận"}

RETRY_REASONS = {"backendError", "internalError", "rateLimitExceeded",
                 "userRateLimitExceeded", "processingFailure"}
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"}


def _reasons(resp: httpx.Response) -> list[str]:
    try:
        errors = resp.json().get("error", {}).get("errors", [])
    except (ValueError, AttributeError):
        return []
    return [e.get("reason", "") for e in errors if isinstance(e, dict)]


def youtube_error(resp: httpx.Response, label: str) -> PublishError:
    reasons = set(_reasons(resp))
    detail = error_excerpt(resp)
    status = resp.status_code
    if status == 401:
        return PublishAuthError(f"{label}: phiên đăng nhập YouTube hết hạn — hãy kết nối lại "
                                "tài khoản.", status=status)
    if reasons & QUOTA_REASONS:
        if "uploadLimitExceeded" in reasons:
            msg = f"{label}: kênh đã đạt giới hạn số video tải lên trong ngày."
        else:
            msg = (f"{label}: hết hạn mức API YouTube trong ngày (mặc định 10.000 đơn vị ≈ "
                   "6 video). Hạn mức đặt lại lúc 0h giờ Thái Bình Dương (≈ 14–15h giờ VN).")
        return PublishQuotaError(msg, status=status, daily=True)
    if "youtubeSignupRequired" in reasons:
        return PublishError(f"{label}: tài khoản chưa có kênh YouTube.", status=status)
    if reasons & {"invalidTitle", "invalidDescription", "invalidTags", "invalidCategoryId",
                  "invalidPublishAt", "invalidVideoMetadata", "mediaBodyRequired"}:
        return PublishError(f"{label}: dữ liệu không hợp lệ ({', '.join(sorted(reasons))}). "
                            f"{detail}", code="invalid", status=status)
    if status in (429, 500, 502, 503, 504) or reasons & RETRY_REASONS:
        return PublishError(f"{label}: máy chủ YouTube tạm lỗi ({status}), sẽ thử lại.",
                            retryable=True, status=status)
    if status == 403:
        return PublishError(f"{label}: bị từ chối (403). {detail}", code="permission",
                            status=status)
    return PublishError(f"{label}: lỗi {status}. {detail}", status=status)


def video_url(video_id: str, short: bool = False) -> str:
    if short:
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


def is_short(req: PublishRequest) -> bool:
    return req.aspect == "9:16" and 0 < req.duration_s <= 180


class YouTubePublisher(Publisher):
    id = "youtube"
    name = "YouTube"
    platform = "youtube"
    description = "Tải video lên kênh YouTube qua YouTube Data API v3 (OAuth của bạn)."

    def constraints(self, options: dict[str, Any] | None = None) -> PublishConstraints:
        return PublishConstraints(
            platform="YouTube", aspect_ratios=["16:9", "9:16", "1:1"],
            min_duration_s=1, max_duration_s=12 * 3600, max_file_size_mb=256_000,
            formats=["mp4", "mov", "webm"], video_codecs=["h264", "hevc", "vp9", "av1"],
            title_required=True, max_title_len=100, title_forbidden_chars="<>",
            max_description_bytes=5000, max_tags_chars=500, max_hashtags=15,
            supports_scheduling=True, min_schedule_ahead_s=60,
            max_schedule_ahead_s=365 * 86400, supports_captions=True,
            caption_formats=["srt", "vtt"], supports_thumbnail=True, supports_playlist=True,
            privacy_options=["public", "unlisted", "private"],
            notes=["Video dọc 9:16 dài tối đa 3 phút được YouTube xếp vào Shorts.",
                   "Tài khoản chưa xác minh số điện thoại chỉ đăng được video ≤ 15 phút và "
                   "không đặt được ảnh thu nhỏ tuỳ chỉnh.",
                   "Dự án Google Cloud chưa được Google duyệt (audit): mọi video tải lên bị "
                   "khoá ở chế độ Riêng tư.",
                   "Hạn mức mặc định 10.000 đơn vị/ngày: mỗi lần tải lên tốn 1.600 "
                   "(≈ 6 video/ngày)."])

    def metadata_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "privacy": {"type": "string", "title": "Chế độ hiển thị", "default": "public",
                        "enum": ["public", "unlisted", "private"],
                        "enumNames": ["Công khai", "Không công khai", "Riêng tư"]},
            "category_id": {"type": "string", "title": "Danh mục", "default": "27",
                            "enum": list(CATEGORIES), "enumNames": list(CATEGORIES.values())},
            "made_for_kids": {"type": "boolean", "title": "Video dành cho trẻ em",
                              "default": False},
            "synthetic_media": {"type": "boolean", "default": True,
                                "title": "Có nội dung tạo bằng AI (giọng đọc / hình ảnh)",
                                "description": "YouTube yêu cầu khai báo nội dung chân thực "
                                               "được tạo hoặc chỉnh sửa bằng AI."},
            "notify_subscribers": {"type": "boolean", "title": "Thông báo cho người đăng ký",
                                   "default": True},
            "upload_captions": {"type": "boolean", "title": "Tải phụ đề SRT lên",
                                "default": True},
            "playlist_id": {"type": "string", "title": "ID danh sách phát (tuỳ chọn)",
                            "default": ""},
            "default_language": {"type": "string", "title": "Ngôn ngữ", "default": "vi"},
        }}

    # ------------------------------------------------------------------ publish

    def _body(self, req: PublishRequest) -> dict[str, Any]:
        o = {**self.default_options(), **req.options}
        lang = o.get("default_language") or "vi"
        status: dict[str, Any] = {
            "privacyStatus": o.get("privacy") or req.privacy,
            "selfDeclaredMadeForKids": bool(o.get("made_for_kids")),
            "containsSyntheticMedia": bool(o.get("synthetic_media", True)),
            "embeddable": True,
        }
        if req.scheduled_at:
            # publishAt only works on private videos; YouTube makes them public at that time.
            status["privacyStatus"] = "private"
            status["publishAt"] = req.scheduled_at.astimezone(UTC).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z")
        tags = [t.strip() for t in req.tags if t.strip()]
        return {"snippet": {"title": safe_title(req.title, 100),
                            "description": req.description, "tags": tags,
                            "categoryId": str(o.get("category_id") or "27"),
                            "defaultLanguage": lang, "defaultAudioLanguage": lang},
                "status": status}

    async def _authed(self, http: httpx.AsyncClient, pctx: PublishContext, method: str,
                      url: str, label: str, **kwargs: Any) -> httpx.Response:
        """Request with a bearer token; on 401 refresh the token once and retry."""
        for attempt in range(2):
            token = await pctx.access_token(force_refresh=attempt > 0)
            headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
            resp = await request(http, method, url, label=label, headers=headers, **kwargs)
            if resp.status_code != 401:
                return resp
            kwargs["headers"] = {k: v for k, v in headers.items() if k != "Authorization"}
        return resp

    async def publish(self, req: PublishRequest, pctx: PublishContext) -> PublishResult:
        state = pctx.state
        warnings: list[str] = list(state.get("warnings", []))
        async with http_client(120) as http:
            if not state.get("video_id"):
                video = await self._upload(http, req, pctx)
                pctx.save_state(video_id=video["id"], uploaded=True,
                                upload_status=(video.get("status") or {}).get("uploadStatus"),
                                privacy=(video.get("status") or {}).get("privacyStatus"))
            video_id = state["video_id"]
            pctx.progress(92, "Ảnh thu nhỏ, phụ đề")
            o = {**self.default_options(), **req.options}
            if req.thumbnail_path and not state.get("thumbnail_done"):
                warn = await self._thumbnail(http, pctx, video_id, req.thumbnail_path)
                if warn:
                    warnings.append(warn)
                pctx.save_state(thumbnail_done=True, warnings=warnings)
            if o.get("upload_captions", True):
                done = dict(state.get("captions_done", {}))
                for lang, path in req.captions.items():
                    if done.get(lang):
                        continue
                    warn = await self._caption(http, pctx, video_id, lang, path)
                    if warn:
                        warnings.append(warn)
                    done[lang] = True
                    pctx.save_state(captions_done=done, warnings=warnings)
            if o.get("playlist_id") and not state.get("playlist_done"):
                warn = await self._playlist(http, pctx, video_id, str(o["playlist_id"]))
                if warn:
                    warnings.append(warn)
                pctx.save_state(playlist_done=True, warnings=warnings)
        requested = o.get("privacy") or req.privacy
        if not req.scheduled_at and requested != "private" and state.get("privacy") == "private":
            warnings.append("YouTube đặt video ở chế độ Riêng tư: dự án Google Cloud của bạn "
                            "chưa được xác minh (audit). Hãy đổi chế độ trong YouTube Studio "
                            "hoặc gửi yêu cầu xác minh.")
        pctx.progress(100, "Đã tải lên YouTube")
        url = video_url(video_id, is_short(req))
        if req.scheduled_at:
            return PublishResult(state="scheduled", remote_id=video_id, url=url,
                                 warnings=warnings,
                                 detail="YouTube sẽ tự công khai video vào giờ đã hẹn.")
        return PublishResult(state="processing", remote_id=video_id, url=url,
                             warnings=warnings, detail="YouTube đang xử lý video.")

    async def _start_session(self, http: httpx.AsyncClient, req: PublishRequest,
                             pctx: PublishContext, total: int) -> str:
        resp = await self._authed(
            http, pctx, "POST", UPLOAD_URL, "YouTube tạo phiên tải lên",
            params={"uploadType": "resumable", "part": "snippet,status",
                    "notifySubscribers": str(bool(
                        req.options.get("notify_subscribers", True))).lower()},
            headers={"X-Upload-Content-Length": str(total),
                     "X-Upload-Content-Type": "video/mp4",
                     "Content-Type": "application/json; charset=UTF-8"},
            content=json.dumps(self._body(req), ensure_ascii=False).encode("utf-8"))
        if resp.status_code != 200 or not resp.headers.get("location"):
            raise youtube_error(resp, "YouTube tạo phiên tải lên")
        session = resp.headers["location"]
        pctx.save_state(session_uri=session, offset=0, total=total,
                        session_started=datetime.now(UTC).isoformat())
        pctx.log("Đã tạo phiên tải lên YouTube (resumable)")
        return session

    async def _query_offset(self, http: httpx.AsyncClient, pctx: PublishContext, session: str,
                            total: int) -> int | dict[str, Any] | None:
        """Ask the server how many bytes it has. Returns the offset, the finished video
        resource, or None when the session is gone."""
        resp = await self._authed(http, pctx, "PUT", session, "YouTube kiểm tra tiến độ",
                                  headers={"Content-Range": f"bytes */{total}",
                                           "Content-Length": "0"})
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code == 308:
            return _range_end(resp)
        if resp.status_code in (404, 410):
            return None
        raise youtube_error(resp, "YouTube kiểm tra tiến độ")

    async def _upload(self, http: httpx.AsyncClient, req: PublishRequest,
                      pctx: PublishContext) -> dict[str, Any]:
        path = req.video_path
        total = path.stat().st_size
        state = pctx.state
        session = state.get("session_uri")
        offset = 0
        if session and state.get("total") == total:
            pctx.log(f"Tiếp tục phiên tải lên đã có (đã gửi {state.get('offset', 0)} byte)")
            got = await self._query_offset(http, pctx, session, total)
            if isinstance(got, dict):
                return got
            if got is None:
                pctx.log("Phiên tải lên cũ đã hết hạn, tạo phiên mới", "warning")
                session = None
            else:
                offset = got
        if not session:
            session = await self._start_session(http, req, pctx, total)
            offset = 0
        retries = 0
        with path.open("rb") as fh:
            while True:
                pctx.check_cancelled()
                fh.seek(offset)
                chunk = fh.read(CHUNK)
                end = offset + len(chunk) - 1
                try:
                    resp = await self._authed(
                        http, pctx, "PUT", session, "YouTube tải lên",
                        headers={"Content-Range": f"bytes {offset}-{end}/{total}",
                                 "Content-Type": "video/mp4"},
                        content=chunk)
                except PublishError as exc:
                    if not exc.retryable:
                        raise
                    resp = None
                    error: PublishError | None = exc
                else:
                    error = None
                    if resp.status_code in (200, 201):
                        pctx.save_state(offset=total)
                        pctx.progress(90, "Đã gửi xong video")
                        return resp.json()
                    if resp.status_code == 308:
                        offset = _range_end(resp)
                        retries = 0
                        pctx.save_state(offset=offset)
                        pctx.progress(90 * offset / max(1, total),
                                      f"Đang tải lên YouTube {offset * 100 // max(1, total)}%")
                        continue
                    if resp.status_code in (404, 410):
                        pctx.log("Phiên tải lên hết hạn, bắt đầu lại", "warning")
                        session = await self._start_session(http, req, pctx, total)
                        offset = 0
                        continue
                    error = youtube_error(resp, "YouTube tải lên")
                    if not error.retryable:
                        raise error
                retries += 1
                if retries > MAX_CHUNK_RETRIES:
                    raise error or PublishError("YouTube tải lên thất bại", retryable=True)
                delay = min(60.0, 2 ** retries)
                pctx.log(f"Lỗi tạm thời khi tải lên ({error.message if error else ''}); thử "
                         f"lại sau {delay:.0f}s", "warning")
                await pctx.sleep(delay)
                got = await self._query_offset(http, pctx, session, total)
                if isinstance(got, dict):
                    return got
                if got is None:
                    session = await self._start_session(http, req, pctx, total)
                    offset = 0
                else:
                    offset = got

    async def _thumbnail(self, http: httpx.AsyncClient, pctx: PublishContext, video_id: str,
                         path: Path) -> str | None:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        resp = await self._authed(http, pctx, "POST", THUMBNAIL_URL, "YouTube ảnh thu nhỏ",
                                  params={"videoId": video_id, "uploadType": "media"},
                                  headers={"Content-Type": mime}, content=path.read_bytes())
        if resp.status_code == 200:
            pctx.log("Đã đặt ảnh thu nhỏ")
            return None
        err = youtube_error(resp, "Ảnh thu nhỏ")
        if err.retryable and err.code != "quota":
            raise err
        if resp.status_code == 403:
            return ("Không đặt được ảnh thu nhỏ: kênh cần xác minh số điện thoại "
                    "(youtube.com/verify) để dùng ảnh thu nhỏ tuỳ chỉnh.")
        return f"Không đặt được ảnh thu nhỏ: {err.message}"

    async def _caption(self, http: httpx.AsyncClient, pctx: PublishContext, video_id: str,
                       lang: str, path: Path) -> str | None:
        boundary = f"vs{uuid.uuid4().hex}"
        meta = {"snippet": {"videoId": video_id, "language": lang, "isDraft": False,
                            "name": "Tiếng Việt" if lang == "vi" else lang}}
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(meta, ensure_ascii=False)}\r\n--{boundary}\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode() + \
            path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        resp = await self._authed(http, pctx, "POST", CAPTIONS_URL, "YouTube phụ đề",
                                  params={"part": "snippet", "uploadType": "multipart"},
                                  headers={"Content-Type":
                                           f"multipart/related; boundary={boundary}"},
                                  content=body)
        if resp.status_code == 200:
            pctx.log(f"Đã tải phụ đề {lang}")
            return None
        err = youtube_error(resp, "Phụ đề")
        if err.retryable and err.code != "quota":
            raise err
        return f"Không tải được phụ đề ({lang}): {err.message}"

    async def _playlist(self, http: httpx.AsyncClient, pctx: PublishContext, video_id: str,
                        playlist_id: str) -> str | None:
        resp = await self._authed(
            http, pctx, "POST", f"{YOUTUBE_API}/playlistItems", "YouTube danh sách phát",
            params={"part": "snippet"},
            json={"snippet": {"playlistId": playlist_id,
                              "resourceId": {"kind": "youtube#video", "videoId": video_id}}})
        if resp.status_code == 200:
            pctx.log("Đã thêm vào danh sách phát")
            return None
        err = youtube_error(resp, "Danh sách phát")
        if err.retryable and err.code != "quota":
            raise err
        return f"Không thêm được vào danh sách phát: {err.message}"

    # ------------------------------------------------------------------ status

    async def status(self, remote_id: str, pctx: PublishContext) -> PublishResult:
        async with http_client(30) as http:
            resp = await self._authed(http, pctx, "GET", f"{YOUTUBE_API}/videos",
                                      "YouTube trạng thái",
                                      params={"part": "status,processingDetails",
                                              "id": remote_id})
        if resp.status_code != 200:
            raise youtube_error(resp, "YouTube trạng thái")
        items = resp.json().get("items") or []
        if not items:
            return PublishResult(state="failed", remote_id=remote_id,
                                 detail="Video không còn trên YouTube (đã bị xoá?)")
        st = items[0].get("status", {})
        upload = st.get("uploadStatus")
        if upload in ("failed", "rejected", "deleted"):
            reason = st.get("failureReason") or st.get("rejectionReason") or upload
            return PublishResult(state="failed", remote_id=remote_id,
                                 detail=f"YouTube từ chối video: {reason}")
        publish_at = st.get("publishAt")
        if publish_at and st.get("privacyStatus") == "private":
            when = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            if when > datetime.now(UTC):
                return PublishResult(state="scheduled", remote_id=remote_id)
        if upload == "uploaded":
            return PublishResult(state="processing", remote_id=remote_id)
        return PublishResult(state="published", remote_id=remote_id,
                             published_at=datetime.now(UTC),
                             detail=f"Chế độ: {st.get('privacyStatus')}")


def _range_end(resp: httpx.Response) -> int:
    m = re.match(r"bytes=0-(\d+)", resp.headers.get("range", ""))
    return int(m.group(1)) + 1 if m else 0
