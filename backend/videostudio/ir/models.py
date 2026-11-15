"""Pydantic models for the Content IR.

These models must stay in sync with ``packages/ir-schema/content-ir.schema.json``
(enforced by ``tests/test_ir_schema.py``) and ``packages/ir-schema/ts/content-ir.ts``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IR_SCHEMA_VERSION = "1.0"

SourceType = Literal["script", "markdown", "docx", "pdf", "website", "text", "other"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IRSource(_Base):
    type: SourceType
    uri: str = Field(description="File path, URL or 'inline:' for pasted text")
    lang: str = Field(default="vi", description="BCP-47 language tag, e.g. 'vi', 'en'")


class _BlockBase(_Base):
    id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ParagraphBlock(_BlockBase):
    kind: Literal["paragraph"] = "paragraph"
    text: str


class HeadingBlock(_BlockBase):
    kind: Literal["heading"] = "heading"
    text: str
    level: int = Field(ge=1, le=6)


class ImageBlock(_BlockBase):
    kind: Literal["image"] = "image"
    src: str
    alt: str = ""
    caption: str | None = None


class TableBlock(_BlockBase):
    kind: Literal["table"] = "table"
    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    caption: str | None = None


class ListItem(_Base):
    text: str
    items: list[ListItem] = Field(default_factory=list)


class ListBlock(_BlockBase):
    kind: Literal["list"] = "list"
    ordered: bool = False
    items: list[ListItem] = Field(default_factory=list)


class CodeBlock(_BlockBase):
    kind: Literal["code"] = "code"
    code: str
    language: str | None = None


class ScreenRecordingBlock(_BlockBase):
    """Placeholder for phase 3 (Playwright recordings of websites)."""

    kind: Literal["screen_recording"] = "screen_recording"
    url: str | None = None
    recipe: str | None = None
    path: str | None = None
    duration_s: float | None = Field(default=None, ge=0)


Block = Annotated[
    ParagraphBlock
    | HeadingBlock
    | ImageBlock
    | TableBlock
    | ListBlock
    | CodeBlock
    | ScreenRecordingBlock,
    Field(discriminator="kind"),
]

BLOCK_KINDS: tuple[str, ...] = (
    "paragraph",
    "heading",
    "image",
    "table",
    "list",
    "code",
    "screen_recording",
)


class Section(_Base):
    id: str
    title: str = ""
    level: int = Field(default=1, ge=1, le=6)
    blocks: list[Block] = Field(default_factory=list)


class ContentIR(_Base):
    schema_version: Literal["1.0"] = IR_SCHEMA_VERSION
    title: str
    source: IRSource
    metadata: dict[str, Any] = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def iter_blocks(self):
        for section in self.sections:
            yield from section.blocks

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {"sections": len(self.sections), "blocks": 0}
        for block in self.iter_blocks():
            counts["blocks"] += 1
            counts[block.kind] = counts.get(block.kind, 0) + 1
        return counts
