"""Offline publishers for demos and tests. They follow the same constraints as the real
platforms, upload in chunks with persisted offsets (so resume works), and write a "post"
that the backend serves at ``/mock-posts/<id>``."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .base import (
    PublishConstraints,
    PublishContext,
    Publisher,
    PublishError,
    PublishRequest,
    PublishResult,
)
from .facebook import FacebookPagePublisher
from .youtube import YouTubePublisher

MOCK_CHUNK = 512 * 1024
MOCK_POST_PREFIX = "/mock-posts/"


class _MockMixin(Publisher):
    mock = True
    chunk_delay_s = 0.02
    real: Publisher

    def constraints(self, options: dict[str, Any] | None = None) -> PublishConstraints:
        return self.real.constraints(options)

    def metadata_schema(self) -> dict[str, Any]:
        return self.real.metadata_schema()

    def _posts_dir(self, pctx: PublishContext):
        assert pctx.data_dir is not None
        d = pctx.data_dir / "mock_posts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def publish(self, req: PublishRequest, pctx: PublishContext) -> PublishResult:
        state = pctx.state
        opts = req.options
        total = req.video_path.stat().st_size
        if not state.get("remote_id"):
            remote_id = f"{self.platform[:2]}-{req.publication_id[:10]}"
            pctx.save_state(remote_id=remote_id, offset=0, total=total)
            pctx.log(f"[giả lập] Tạo phiên tải lên {remote_id}")
        else:
            pctx.log(f"[giả lập] Tiếp tục phiên {state['remote_id']} từ byte "
                     f"{state.get('offset', 0)}")
        digest = hashlib.sha256()
        with req.video_path.open("rb") as fh:
            digest.update(fh.read(int(state.get("offset", 0))))
            while state["offset"] < total:
                pctx.check_cancelled()
                fail_at = opts.get("mock_fail_at")
                if fail_at is not None and not state.get("mock_failed") and \
                        state["offset"] >= total * float(fail_at):
                    pctx.save_state(mock_failed=True)
                    raise PublishError("[giả lập] Mất kết nối giữa chừng", retryable=True,
                                       code="network")
                if opts.get("mock_error"):
                    raise PublishError(f"[giả lập] {opts['mock_error']}",
                                       retryable=bool(opts.get("mock_error_retryable")))
                chunk = fh.read(MOCK_CHUNK)
                digest.update(chunk)
                await pctx.sleep(self.chunk_delay_s)
                pctx.save_state(offset=state["offset"] + len(chunk))
                pctx.progress(90 * state["offset"] / max(1, total),
                              f"[giả lập] Đang tải lên {state['offset'] * 100 // total}%")
        remote_id = state["remote_id"]
        token = await pctx.access_token()
        post = {"id": remote_id, "platform": self.platform, "publisher": self.id,
                "publication_id": req.publication_id, "account": req.account.name,
                "title": req.title, "description": req.description, "tags": req.tags,
                "privacy": opts.get("privacy", req.privacy), "format": opts.get("format",
                                                                               "video"),
                "options": {k: v for k, v in opts.items() if not k.startswith("mock_")},
                "video": str(req.video_path), "size": total, "sha256": digest.hexdigest(),
                "duration_s": req.duration_s, "aspect": req.aspect,
                "thumbnail": str(req.thumbnail_path) if req.thumbnail_path else None,
                "captions": {k: str(v) for k, v in req.captions.items()},
                "scheduled_at": req.scheduled_at.isoformat() if req.scheduled_at else None,
                "created_at": datetime.now(UTC).isoformat(),
                "token_ok": token.startswith("mock-")}
        (self._posts_dir(pctx) / f"{remote_id}.json").write_text(
            json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
        pctx.progress(100, "[giả lập] Đã đăng")
        url = f"{MOCK_POST_PREFIX}{remote_id}"
        if req.scheduled_at:
            return PublishResult(state="scheduled", remote_id=remote_id, url=url,
                                 detail="[giả lập] Nền tảng sẽ đăng vào giờ đã hẹn.")
        return PublishResult(state="published", remote_id=remote_id, url=url,
                             published_at=datetime.now(UTC))

    async def status(self, remote_id: str, pctx: PublishContext) -> PublishResult:
        path = self._posts_dir(pctx) / f"{remote_id}.json"
        if not path.is_file():
            return PublishResult(state="failed", remote_id=remote_id,
                                 detail="[giả lập] Bài đăng không còn")
        post = json.loads(path.read_text(encoding="utf-8"))
        at = post.get("scheduled_at")
        if at and datetime.fromisoformat(at) > datetime.now(UTC):
            return PublishResult(state="scheduled", remote_id=remote_id)
        return PublishResult(state="published", remote_id=remote_id,
                             published_at=datetime.now(UTC))


class MockYouTubePublisher(_MockMixin):
    id = "mock_youtube"
    name = "YouTube (giả lập)"
    platform = "youtube"
    description = "Giả lập YouTube để thử quy trình đăng mà không cần tài khoản thật."
    real = YouTubePublisher()


class MockFacebookPublisher(_MockMixin):
    id = "mock_facebook"
    name = "Trang Facebook (giả lập)"
    platform = "facebook"
    description = "Giả lập Trang Facebook để thử quy trình đăng mà không cần tài khoản thật."
    real = FacebookPagePublisher()


def builtin_publishers() -> list[Publisher]:
    return [YouTubePublisher(), FacebookPagePublisher(), MockYouTubePublisher(),
            MockFacebookPublisher()]
