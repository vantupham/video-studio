"""Extension-point interfaces.

``InputAdapter`` (script, markdown), providers and renderers have implementations; the
publisher interface is defined so phase 3 can plug implementations in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from videostudio.ir import ContentIR
from videostudio.pipeline.render_types import Renderer, RenderPlan, RenderResult
from videostudio.providers.base import (
    AlignmentProvider,
    BaseProvider,
    ChatMessage,
    LLMProvider,
    MediaProvider,
    TTSProvider,
    WordTiming,
)


class JobCancelled(Exception):
    """Raised from ``check_cancelled()`` when the user cancelled the running job."""


@dataclass
class SourceRef:
    """What an input adapter receives: a pointer to the raw content."""

    type: Literal["file", "text", "url"]
    uri: str
    path: Path | None = None
    filename: str | None = None
    mime: str | None = None
    lang: str = "vi"

    @property
    def suffix(self) -> str:
        name = self.filename or (self.path.name if self.path else self.uri)
        return Path(name).suffix.lower()


@dataclass
class JobContext:
    """Passed to plugins running inside a job: progress, logs, cancellation."""

    job_id: str
    project_dir: Path | None
    config: dict[str, Any] = field(default_factory=dict)
    progress_cb: Callable[[int, str | None], None] | None = None
    log_cb: Callable[[str, str], None] | None = None
    cancelled_cb: Callable[[], bool] | None = None
    # (capability, instance id or None for the default) -> ResolvedProvider
    resolve_provider: Callable[[str, str | None], Any] | None = None

    def provider(self, capability: str, instance_id: str | None = None) -> Any:
        if self.resolve_provider is None:
            raise RuntimeError(f"No provider resolver available for '{capability}'")
        return self.resolve_provider(capability, instance_id)

    def report_progress(self, percent: float, message: str | None = None) -> None:
        if self.progress_cb:
            self.progress_cb(max(0, min(100, int(percent))), message)

    def log(self, message: str, level: str = "info") -> None:
        if self.log_cb:
            self.log_cb(message, level)

    def is_cancelled(self) -> bool:
        return bool(self.cancelled_cb and self.cancelled_cb())

    def check_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelled()


@runtime_checkable
class InputAdapter(Protocol):
    """Turns a source (file / text / url) into a :class:`ContentIR`."""

    id: str
    name: str
    description: str
    file_extensions: tuple[str, ...]

    def can_handle(self, source: SourceRef) -> bool: ...

    def config_schema(self) -> dict[str, Any]:
        """JSON Schema of the adapter options; the UI renders a form from it."""
        ...

    async def ingest(self, source: SourceRef, ctx: JobContext) -> ContentIR: ...


# --------------------------------------------------------------------------------------
# Providers (LLM / TTS / alignment / media) live in ``videostudio.providers``; renderers
# in ``videostudio.pipeline.render_types``. They are re-exported here for plugin authors.
# Publishers are still an interface only (phase 3).
# --------------------------------------------------------------------------------------


class PublishConstraints(BaseModel):
    max_duration_s: float | None = None
    max_file_size_mb: float | None = None
    aspect_ratios: list[str] = Field(default_factory=list)
    max_title_len: int | None = None
    max_description_len: int | None = None
    supports_scheduling: bool = False


class PublishRequest(BaseModel):
    video_path: Path
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    thumbnail_path: Path | None = None
    scheduled_at: datetime | None = None
    privacy: Literal["public", "unlisted", "private"] = "private"
    options: dict[str, Any] = Field(default_factory=dict)


class PublishStatus(BaseModel):
    remote_id: str
    state: Literal["uploading", "processing", "scheduled", "published", "failed"]
    url: str | None = None
    detail: str | None = None


@runtime_checkable
class Publisher(Protocol):
    id: str
    name: str

    def config_schema(self) -> dict[str, Any]: ...

    def constraints(self) -> PublishConstraints: ...

    async def publish(self, request: PublishRequest, ctx: JobContext) -> PublishStatus: ...

    async def status(self, remote_id: str) -> PublishStatus: ...


__all__ = [
    "AlignmentProvider",
    "BaseProvider",
    "ChatMessage",
    "InputAdapter",
    "JobCancelled",
    "JobContext",
    "LLMProvider",
    "MediaProvider",
    "PublishConstraints",
    "PublishRequest",
    "PublishStatus",
    "Publisher",
    "RenderPlan",
    "RenderResult",
    "Renderer",
    "SourceRef",
    "TTSProvider",
    "WordTiming",
]
