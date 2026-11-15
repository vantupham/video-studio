"""Publisher plugin interface.

A publisher uploads one rendered video to one connected account. It must be *resumable*
and *idempotent*: everything needed to continue (upload session URI, byte offset, remote
video id, which post-upload steps are done) is written through
:meth:`PublishContext.save_state` as soon as it is known, and :meth:`Publisher.publish`
called again with that state continues instead of uploading a second copy.
"""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from videostudio.plugins.base import JobCancelled
from videostudio.providers.errors import redact

PLATFORMS = {"youtube": "YouTube", "facebook": "Facebook"}


class PublishConstraints(BaseModel):
    """What a platform accepts. Checked before every publish (see ``validation``)."""

    platform: str
    format: str = "video"
    aspect_ratios: list[str] = Field(default_factory=list)  # accepted; empty = any
    preferred_aspect: str | None = None
    min_duration_s: float | None = None
    max_duration_s: float | None = None
    max_file_size_mb: float | None = None
    formats: list[str] = Field(default_factory=lambda: ["mp4"])
    video_codecs: list[str] = Field(default_factory=lambda: ["h264"])
    title_required: bool = False
    max_title_len: int | None = None
    title_forbidden_chars: str = ""
    max_description_len: int | None = None
    max_description_bytes: int | None = None
    max_tags_chars: int | None = None
    max_hashtags: int | None = None
    supports_scheduling: bool = False
    min_schedule_ahead_s: int | None = None
    max_schedule_ahead_s: int | None = None
    supports_captions: bool = False
    caption_formats: list[str] = Field(default_factory=list)
    supports_thumbnail: bool = False
    supports_playlist: bool = False
    privacy_options: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AccountRef(BaseModel):
    id: str
    platform: str
    external_id: str
    name: str
    kind: str = "channel"
    meta: dict[str, Any] = Field(default_factory=dict)


class PublishRequest(BaseModel):
    publication_id: str  # idempotency key
    account: AccountRef
    video_path: Path
    duration_s: float = 0
    width: int = 0
    height: int = 0
    aspect: str = "16:9"
    size_bytes: int = 0
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    thumbnail_path: Path | None = None
    captions: dict[str, Path] = Field(default_factory=dict)  # language → .srt
    scheduled_at: datetime | None = None  # UTC; platform-side scheduling
    privacy: Literal["public", "unlisted", "private"] = "public"
    options: dict[str, Any] = Field(default_factory=dict)


PublishState = Literal["processing", "scheduled", "published", "failed"]


class PublishResult(BaseModel):
    state: PublishState
    remote_id: str | None = None
    url: str | None = None
    detail: str | None = None
    published_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)


class PublishError(Exception):
    """User-facing (Vietnamese) publish failure. ``retryable`` errors are retried with
    backoff; ``code`` drives special handling (auth → reconnect, quota → retry tomorrow)."""

    def __init__(self, message: str, *, retryable: bool = False, code: str = "error",
                 status: int | None = None):
        super().__init__(message)
        self.message = redact(message)
        self.user_message = self.message
        self.retryable = retryable
        self.code = code
        self.status = status


class PublishAuthError(PublishError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message, retryable=False, code="auth", status=status)


class PublishQuotaError(PublishError):
    """``daily``: the platform's daily quota is used up → retry after its reset."""

    def __init__(self, message: str, status: int | None = None, daily: bool = False):
        super().__init__(message, retryable=True, code="quota", status=status)
        self.daily = daily


@dataclass
class PublishContext:
    """What the service gives a publisher for one attempt."""

    state: dict[str, Any]
    _save: Callable[[dict[str, Any]], None]
    _token: Callable[[bool], Awaitable[str]]
    _progress: Callable[[float, str | None], None] = lambda p, m: None
    _log: Callable[[str, str], None] = lambda m, level: None
    _cancelled: Callable[[], bool] = lambda: False
    data_dir: Path | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    extras: dict[str, Any] = field(default_factory=dict)

    def save_state(self, **changes: Any) -> None:
        """Merge and persist immediately (before the next network call)."""
        self.state.update(changes)
        self._save(dict(self.state))

    async def access_token(self, force_refresh: bool = False) -> str:
        return await self._token(force_refresh)

    def progress(self, percent: float, message: str | None = None) -> None:
        self._progress(max(0.0, min(100.0, percent)), message)

    def log(self, message: str, level: str = "info") -> None:
        self._log(redact(message), level)

    def check_cancelled(self) -> None:
        if self._cancelled():
            raise JobCancelled()


class Publisher(ABC):
    id: str
    name: str
    platform: str
    mock: bool = False
    description: str = ""

    @abstractmethod
    def constraints(self, options: dict[str, Any] | None = None) -> PublishConstraints: ...

    def metadata_schema(self) -> dict[str, Any]:
        """JSON schema of platform-specific options for the metadata editor."""
        return {"type": "object", "properties": {}}

    def default_options(self) -> dict[str, Any]:
        props = self.metadata_schema().get("properties", {})
        return {k: v["default"] for k, v in props.items() if "default" in v}

    @abstractmethod
    async def publish(self, req: PublishRequest, pctx: PublishContext) -> PublishResult: ...

    async def status(self, remote_id: str, pctx: PublishContext) -> PublishResult:
        return PublishResult(state="published", remote_id=remote_id)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "platform": self.platform, "mock": self.mock,
                "description": self.description, "metadata_schema": self.metadata_schema(),
                "constraints": self.constraints().model_dump()}


# ------------------------------------------------------------------ HTTP helpers

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


def http_client(timeout: float = 60.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0),
                             follow_redirects=True)


def error_excerpt(response: httpx.Response, limit: int = 300) -> str:
    try:
        data = response.json()
    except ValueError:
        return redact(response.text[:limit])
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        msg = err.get("message") or err.get("error_description") or json.dumps(err)
        return redact(str(msg)[:limit])
    if isinstance(data, dict) and data.get("error_description"):
        return redact(str(data["error_description"])[:limit])
    return redact(str(err)[:limit])


async def request(client: httpx.AsyncClient, method: str, url: str, *, label: str,
                  **kwargs: Any) -> httpx.Response:
    """One HTTP call with transport errors mapped to retryable :class:`PublishError`.
    The caller inspects the status code."""
    try:
        return await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise PublishError(f"{label}: quá thời gian chờ", retryable=True, code="timeout") \
            from exc
    except httpx.TransportError as exc:
        raise PublishError(f"{label}: mất kết nối ({type(exc).__name__})", retryable=True,
                           code="network") from exc


def safe_title(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]
