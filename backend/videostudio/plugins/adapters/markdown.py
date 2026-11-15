"""Markdown adapter (CommonMark + GFM tables/strikethrough) based on markdown-it-py."""

from __future__ import annotations

import re
from typing import Any

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from videostudio.ir import (
    Block,
    CodeBlock,
    ContentIR,
    HeadingBlock,
    ImageBlock,
    IRSource,
    ListBlock,
    ListItem,
    ParagraphBlock,
    Section,
    TableBlock,
)
from videostudio.plugins.base import JobContext, SourceRef
from videostudio.textutil import collapse_spaces, estimate_duration_s, word_count

from .common import (
    LANGUAGE_FIELD,
    TITLE_FIELD,
    apply_defaults,
    file_stem_title,
    read_source_text,
)

FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONT_MATTER.match(text)
    if not match:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return {str(k): v for k, v in data.items()}, text[match.end():]


def inline_text(token: Token) -> str:
    parts: list[str] = []
    for child in token.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type == "softbreak":
            parts.append(" ")
        elif child.type == "hardbreak":
            parts.append("\n")
    lines = [collapse_spaces(line) for line in "".join(parts).split("\n")]
    return "\n".join(line for line in lines if line)


def inline_images(token: Token) -> list[ImageBlock]:
    images = []
    for child in token.children or []:
        if child.type == "image":
            alt = "".join(c.content for c in child.children or []) or child.content
            title = child.attrGet("title")
            images.append(
                ImageBlock(src=str(child.attrGet("src") or ""), alt=alt, caption=title or None)
            )
    return images


class MarkdownAdapter:
    id = "markdown"
    name = "Markdown"
    description = "Tài liệu Markdown: tiêu đề, đoạn văn, danh sách, hình ảnh, bảng, code."
    file_extensions = (".md", ".markdown", ".mdown", ".mkd")

    def __init__(self) -> None:
        self._md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])

    def can_handle(self, source: SourceRef) -> bool:
        return source.suffix in self.file_extensions or (source.mime or "") == "text/markdown"

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": TITLE_FIELD,
                "language": LANGUAGE_FIELD,
                "section_level": {
                    "type": "integer",
                    "title": "Cấp tiêu đề để tách phần",
                    "description": "Tiêu đề có cấp nhỏ hơn hoặc bằng giá trị này mở một phần mới.",
                    "enum": [1, 2, 3],
                    "x-enumLabels": ["H1", "H1–H2", "H1–H3"],
                    "default": 2,
                },
                "include_code": {
                    "type": "boolean",
                    "title": "Giữ khối code",
                    "default": True,
                },
                "keep_heading_blocks": {
                    "type": "boolean",
                    "title": "Giữ tiêu đề như một block",
                    "default": True,
                },
            },
            "additionalProperties": False,
        }

    async def ingest(self, source: SourceRef, ctx: JobContext) -> ContentIR:
        cfg = apply_defaults(self.config_schema(), ctx.config)
        ctx.report_progress(5, "Đọc file Markdown")
        raw = read_source_text(source)
        front_matter, body = split_front_matter(raw)
        tokens = self._md.parse(body)
        ctx.check_cancelled()
        ctx.report_progress(30, "Phân tích cấu trúc")

        builder = _IRBuilder(
            section_level=int(cfg["section_level"]),
            include_code=bool(cfg["include_code"]),
            keep_heading_blocks=bool(cfg["keep_heading_blocks"]),
        )
        builder.walk(tokens)
        ctx.report_progress(80, "Đã dựng Content IR")

        title = (
            cfg.get("title")
            or str(front_matter.get("title") or "")
            or builder.first_h1
            or file_stem_title(source)
            or "Tài liệu Markdown"
        )
        text_parts = []
        for section in builder.sections:
            for block in section.blocks:
                if isinstance(block, ParagraphBlock | HeadingBlock):
                    text_parts.append(block.text)
        words = word_count(" ".join(text_parts))
        metadata: dict[str, Any] = {
            "adapter": self.id,
            "word_count": words,
            "estimated_duration_s": estimate_duration_s(words),
        }
        if front_matter:
            metadata["front_matter"] = _jsonable(front_matter)
        lang = str(front_matter.get("lang") or cfg["language"])
        return ContentIR(
            title=title,
            source=IRSource(type="markdown", uri=source.uri, lang=lang),
            metadata=metadata,
            sections=builder.sections,
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


class _IRBuilder:
    def __init__(self, section_level: int, include_code: bool, keep_heading_blocks: bool):
        self.section_level = section_level
        self.include_code = include_code
        self.keep_heading_blocks = keep_heading_blocks
        self.sections: list[Section] = []
        self.first_h1: str | None = None
        self._quote_depth = 0

    def _section(self) -> Section:
        if not self.sections:
            self._new_section("", 1)
        return self.sections[-1]

    def _new_section(self, title: str, level: int) -> Section:
        section = Section(id=f"s{len(self.sections) + 1}", title=title, level=level)
        self.sections.append(section)
        return section

    def _add(self, block: Block) -> None:
        section = self._section()
        block.id = f"{section.id}-b{len(section.blocks) + 1}"
        if self._quote_depth:
            block.meta["quote"] = True
        section.blocks.append(block)

    def walk(self, tokens: list[Token]) -> None:
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "heading_open":
                level = int(tok.tag[1:])
                text = inline_text(tokens[i + 1])
                if level == 1 and self.first_h1 is None:
                    self.first_h1 = text
                if level <= self.section_level:
                    self._new_section(text, level)
                if self.keep_heading_blocks:
                    self._add(HeadingBlock(text=text, level=level))
                i += 3
            elif tok.type == "paragraph_open":
                inline = tokens[i + 1]
                text = inline_text(inline)
                if text:
                    self._add(ParagraphBlock(text=text))
                for image in inline_images(inline):
                    self._add(image)
                i += 3
            elif tok.type in ("bullet_list_open", "ordered_list_open"):
                block, i = self._parse_list(tokens, i)
                self._add(block)
            elif tok.type in ("fence", "code_block"):
                if self.include_code:
                    lang = (tok.info or "").strip().split(" ")[0] or None
                    self._add(CodeBlock(code=tok.content.rstrip("\n"), language=lang))
                i += 1
            elif tok.type == "table_open":
                block, i = self._parse_table(tokens, i)
                self._add(block)
            elif tok.type == "blockquote_open":
                self._quote_depth += 1
                i += 1
            elif tok.type == "blockquote_close":
                self._quote_depth -= 1
                i += 1
            else:
                i += 1

    def _parse_list(self, tokens: list[Token], start: int) -> tuple[ListBlock, int]:
        open_tok = tokens[start]
        close_type = open_tok.type.replace("_open", "_close")
        block = ListBlock(ordered=open_tok.type == "ordered_list_open")
        i = start + 1
        current: ListItem | None = None
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == close_type and tok.level == open_tok.level:
                return block, i + 1
            if tok.type == "list_item_open":
                current = ListItem(text="")
                block.items.append(current)
                i += 1
            elif tok.type == "inline" and current is not None:
                text = inline_text(tok)
                current.text = f"{current.text} {text}".strip() if current.text else text
                i += 1
            elif tok.type in ("bullet_list_open", "ordered_list_open") and current is not None:
                nested, i = self._parse_list(tokens, i)
                current.items.extend(nested.items)
            elif tok.type in ("fence", "code_block") and current is not None:
                code = tok.content.strip()
                current.text = f"{current.text}\n{code}".strip()
                i += 1
            else:
                i += 1
        return block, i

    def _parse_table(self, tokens: list[Token], start: int) -> tuple[TableBlock, int]:
        block = TableBlock()
        in_head = False
        row: list[str] | None = None
        i = start + 1
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "table_close":
                return block, i + 1
            if tok.type == "thead_open":
                in_head = True
            elif tok.type == "thead_close":
                in_head = False
            elif tok.type == "tr_open":
                row = []
            elif tok.type == "tr_close" and row is not None:
                if in_head and not block.header:
                    block.header = row
                else:
                    block.rows.append(row)
                row = None
            elif tok.type == "inline" and row is not None:
                row.append(inline_text(tok))
            i += 1
        return block, i
