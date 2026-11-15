"""Extension-point interfaces.

Phase 0 implements ``InputAdapter`` for real (script, markdown). The other
interfaces (LLM, TTS, renderer, publisher) are defined here so later phases can
plug implementations in without changing the core.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from videostudio.ir import ContentIR


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
# Interfaces for later phases (no implementation in phase 0).
# --------------------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    id: str
    name: str

    def config_schema(self) -> dict[str, Any]: ...

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.7,
    ) -> str: ...

    def stream(self, messages: list[ChatMessage]) -> AsyncIterator[str]: ...


class WordTiming(BaseModel):
    text: str
    start_s: float
    end_s: float


class TTSResult(BaseModel):
    audio_path: Path
    duration_s: float
    words: list[WordTiming] = Field(default_factory=list)


@runtime_checkable
class TTSProvider(Protocol):
    id: str
    name: str
    languages: tuple[str, ...]

    def config_schema(self) -> dict[str, Any]: ...

    async def list_voices(self, lang: str | None = None) -> list[dict[str, Any]]: ...

    async def synthesize(
        self, text: str, *, voice: str, out_path: Path, ctx: JobContext
    ) -> TTSResult: ...


class RenderRequest(BaseModel):
    project_id: str
    style_id: str
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    out_path: Path
    options: dict[str, Any] = Field(default_factory=dict)


class RenderResult(BaseModel):
    video_path: Path
    duration_s: float
    thumbnail_path: Path | None = None


@runtime_checkable
class Renderer(Protocol):
    id: str
    name: str

    def config_schema(self) -> dict[str, Any]: ...

    async def render(self, request: RenderRequest, ctx: JobContext) -> RenderResult: ...


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
