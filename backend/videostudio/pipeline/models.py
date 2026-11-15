"""Storyboard models — the editable video plan between the LLM and the renderer.

The JSON Schema in ``packages/ir-schema/storyboard.schema.json`` is generated from these
models (``python -m videostudio.pipeline.models > …``) and the TS types in
``packages/ir-schema/ts/storyboard.ts`` mirror them; a test keeps all three in sync.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STORYBOARD_VERSION = 1
VISUAL_TYPES = ("title_card", "text_card", "image", "stock", "solid")
ASPECTS = ("16:9", "9:16", "1:1")

VisualType = Literal["title_card", "text_card", "image", "stock", "solid"]
Aspect = Literal["16:9", "9:16", "1:1"]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class Visual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: VisualType = "text_card"
    title: str | None = None
    subtitle: str | None = None
    bullets: list[str] = Field(default_factory=list, max_length=8)
    image: str | None = Field(
        default=None, description="Image path relative to the data dir (uploaded/IR asset)")
    query: str | None = Field(default=None, description="Stock media search query")
    color: str | None = Field(default=None, description="Background colour #RRGGBB")

    @field_validator("color")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is not None and not _HEX.match(v):
            raise ValueError("color must be #RRGGBB")
        return v


class Overlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text", "lower_third"] = "text"
    text: str = Field(min_length=1)
    position: Literal["top", "center", "bottom"] = "top"


class Scene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
    narration: str = Field(default="", max_length=4000)
    visual: Visual = Field(default_factory=Visual)
    overlays: list[Overlay] = Field(default_factory=list)
    duration: float | Literal["auto"] = Field(
        default="auto", description="'auto' = follow the narration; number = fixed seconds")
    source_block_ids: list[str] = Field(default_factory=list)

    @field_validator("duration")
    @classmethod
    def _duration(cls, v: float | str) -> float | str:
        if v != "auto" and not (0.5 <= float(v) <= 600):
            raise ValueError("fixed duration must be between 0.5 and 600 seconds")
        return v


class Storyboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = STORYBOARD_VERSION
    title: str = Field(min_length=1, max_length=512)
    language: str = "vi"
    style: str = "explainer_16x9"
    aspect: Aspect = "16:9"
    scenes: list[Scene] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique_ids(self) -> Storyboard:
        ids = [s.id for s in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("scene ids must be unique")
        return self


# ----------------------------------------------------------------------------- LLM output


class DraftScene(BaseModel):
    """What the LLM returns for one scene (validated, then converted to :class:`Scene`)."""

    narration: str = Field(min_length=1, description="Lời thoại đọc cho cảnh này")
    visual_type: VisualType = Field(default="text_card", description="Kiểu hình ảnh")
    title: str | None = Field(default=None, description="Tiêu đề hiển thị trên màn hình")
    bullets: list[str] = Field(default_factory=list, description="Các ý ngắn hiển thị")
    image_query: str | None = Field(default=None,
                                    description="Từ khoá tìm ảnh minh hoạ (tiếng Anh)")


class ScriptDraft(BaseModel):
    title: str = Field(min_length=1)
    scenes: list[DraftScene] = Field(min_length=1)


# ----------------------------------------------------------------------------- settings


class ProviderChoice(BaseModel):
    instance_id: str | None = None
    model: str | None = None
    voice: str | None = None


class GenerationSettings(BaseModel):
    """Options of the "Tạo video" dialog. Missing values fall back to project settings,
    then to the defaults from the style / provider defaults."""

    style: str = "explainer_16x9"
    aspect: Aspect | None = None
    target_duration_s: int = Field(default=60, ge=5, le=1800)
    language: str = "vi"
    tone: str = "thân thiện, dễ hiểu"
    use_as_is: bool = False
    max_scenes: int = Field(default=12, ge=1, le=60)
    llm: ProviderChoice = Field(default_factory=ProviderChoice)
    tts: ProviderChoice = Field(default_factory=ProviderChoice)
    alignment: ProviderChoice = Field(default_factory=ProviderChoice)
    media: ProviderChoice = Field(default_factory=ProviderChoice)
    music_asset_id: str | None = None
    music_volume: float = Field(default=0.18, ge=0, le=1)
    captions: bool = True
    burn_subtitles: bool = True


def storyboard_json_schema() -> dict[str, Any]:
    schema = Storyboard.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://videostudio.local/schemas/storyboard.schema.json"
    return schema


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(storyboard_json_schema(), ensure_ascii=False, indent=2))
