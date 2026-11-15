"""Renderer interface. The built-in implementation is the FFmpeg renderer
(``videostudio.pipeline.renderer_ffmpeg``); a future Remotion renderer can implement the
same protocol."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from videostudio.providers.base import WordTiming

from .models import Scene

if TYPE_CHECKING:
    from videostudio.plugins.base import JobContext


class SceneMedia(BaseModel):
    """A scene plus everything generated for it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    scene: Scene
    audio_path: Path | None = None
    audio_duration_s: float = 0.0
    words: list[WordTiming] = Field(default_factory=list)
    image_path: Path | None = None
    duration_s: float = 0.0  # final on-screen duration


class Branding(BaseModel):
    logo_path: Path | None = None
    logo_position: str = "top-right"
    logo_scale: float = 0.12
    logo_opacity: float = 0.85
    intro_text: str = ""
    outro_text: str = ""
    card_seconds: float = 2.5


class RenderPlan(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    title: str
    style_id: str
    style_data: dict[str, Any]
    style_dirs: list[Path]
    aspect: str
    language: str = "vi"
    scenes: list[SceneMedia]
    work_dir: Path  # cache for intermediate segments (reused between renders)
    out_dir: Path
    basename: str = "video"
    branding: Branding = Field(default_factory=Branding)
    music_path: Path | None = None
    music_volume: float = 0.18
    burn_subtitles: bool = True
    preview: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class RenderResult(BaseModel):
    video_path: Path
    duration_s: float
    width: int
    height: int
    encoder: str
    subtitles: dict[str, Path] = Field(default_factory=dict)
    thumbnail_path: Path | None = None
    # [{"scene_id", "kind": intro|scene|outro, "start", "end"}] in seconds
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    transition: str = "none"


@runtime_checkable
class Renderer(Protocol):
    id: str
    name: str

    def config_schema(self) -> dict[str, Any]: ...

    async def render(self, plan: RenderPlan, ctx: JobContext) -> RenderResult: ...
