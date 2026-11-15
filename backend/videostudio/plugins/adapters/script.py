"""Plain-text script adapter: paragraphs become blocks, scene markers become sections."""

from __future__ import annotations

import re
from typing import Any

from videostudio.ir import ContentIR, IRSource, ParagraphBlock, Section
from videostudio.plugins.base import JobContext, SourceRef
from videostudio.textutil import collapse_spaces, estimate_duration_s, word_count

from .common import (
    LANGUAGE_FIELD,
    TITLE_FIELD,
    apply_defaults,
    file_stem_title,
    read_source_text,
)

SECTION_MARKER = re.compile(
    r"^\s*(?:#{1,6}\s+\S.*|(?:cảnh|phần|chương|đoạn|scene|part|chapter)\s*\d+\b.*)$",
    re.IGNORECASE,
)


class ScriptAdapter:
    id = "script"
    name = "Kịch bản (văn bản thường)"
    description = (
        "Văn bản thuần: mỗi đoạn thành một block. Dòng 'Cảnh 1', 'Phần 2', '# Tiêu đề' "
        "mở một phần mới."
    )
    file_extensions = (".txt", ".text", ".script")

    def can_handle(self, source: SourceRef) -> bool:
        return source.type == "text" or source.suffix in self.file_extensions

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": TITLE_FIELD,
                "language": LANGUAGE_FIELD,
                "split_mode": {
                    "type": "string",
                    "title": "Tách đoạn theo",
                    "enum": ["blank_line", "line"],
                    "x-enumLabels": ["Dòng trống", "Mỗi dòng"],
                    "default": "blank_line",
                },
                "detect_sections": {
                    "type": "boolean",
                    "title": "Nhận diện cảnh / phần",
                    "description": "Dòng 'Cảnh 1', 'Phần 2', 'Scene 3' hoặc '# ...' mở phần mới.",
                    "default": True,
                },
            },
            "additionalProperties": False,
        }

    async def ingest(self, source: SourceRef, ctx: JobContext) -> ContentIR:
        cfg = apply_defaults(self.config_schema(), ctx.config)
        ctx.report_progress(5, "Đọc kịch bản")
        text = read_source_text(source)
        ctx.check_cancelled()

        sections: list[Section] = []
        current: Section | None = None
        buffer: list[str] = []

        def new_section(title: str) -> Section:
            section = Section(id=f"s{len(sections) + 1}", title=title, level=1)
            sections.append(section)
            return section

        def flush() -> None:
            nonlocal current
            if not buffer:
                return
            paragraph = collapse_spaces(" ".join(buffer))
            buffer.clear()
            if not paragraph:
                return
            if current is None:
                current = new_section("")
            current.blocks.append(
                ParagraphBlock(id=f"{current.id}-b{len(current.blocks) + 1}", text=paragraph)
            )

        for line in text.split("\n"):
            stripped = line.strip()
            if cfg["detect_sections"] and stripped and SECTION_MARKER.match(stripped):
                flush()
                current = new_section(stripped.lstrip("#").strip())
                continue
            if cfg["split_mode"] == "line":
                buffer.append(stripped)
                flush()
            elif stripped:
                buffer.append(stripped)
            else:
                flush()
        flush()
        ctx.report_progress(80, "Đã tách đoạn")

        all_text = " ".join(b.text for s in sections for b in s.blocks)  # type: ignore[union-attr]
        first_line = next((ln.strip() for ln in text.split("\n") if ln.strip()), "Kịch bản")
        title = cfg.get("title") or file_stem_title(source) or first_line[:80]
        words = word_count(all_text)
        return ContentIR(
            title=title,
            source=IRSource(type="script", uri=source.uri, lang=cfg["language"]),
            metadata={
                "adapter": self.id,
                "word_count": words,
                "char_count": len(all_text),
                "estimated_duration_s": estimate_duration_s(words),
            },
            sections=sections,
        )
