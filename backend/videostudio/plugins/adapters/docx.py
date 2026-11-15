"""Word (.docx) adapter based on python-docx.

* Headings come from the paragraph style ("Heading N", "Title") or an outline level set on
  the paragraph or anywhere in its style chain; optionally a short paragraph that is bold
  from start to end is treated as a heading too.
* Lists are paragraphs with numbering (direct or through the style); bullet vs ordered is
  read from the numbering definition.
* Embedded images are written to ``media/`` next to the source file, so the storyboard
  resolver finds them like Markdown images.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from videostudio.ir import (
    Block,
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
from videostudio.textutil import collapse_spaces, estimate_duration_s, normalize_text, word_count

from .common import LANGUAGE_FIELD, TITLE_FIELD, apply_defaults, file_stem_title

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
IMAGE_EXTS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
              "image/bmp": ".bmp", "image/webp": ".webp", "image/tiff": ".tiff",
              "image/x-png": ".png"}
_HEADING_STYLE = re.compile(r"^(?:heading|tiêu đề|überschrift)\s*(\d)$", re.IGNORECASE)
_CAPTION_STYLES = {"caption", "chú thích"}
_ORDERED_FORMATS = {"decimal", "lowerLetter", "upperLetter", "lowerRoman", "upperRoman",
                    "decimalZero", "ordinal"}


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _outline_level(ppr) -> int | None:
    if ppr is None:
        return None
    el = ppr.find(_w("outlineLvl"))
    if el is None:
        return None
    try:
        val = int(el.get(_w("val")))
    except (TypeError, ValueError):
        return None
    return val + 1 if 0 <= val < 9 else None


def _style_chain(style):
    seen = 0
    while style is not None and seen < 10:
        yield style
        style = style.base_style
        seen += 1


def heading_level(paragraph) -> int | None:
    level = _outline_level(paragraph._p.pPr)
    if level:
        return min(level, 6)
    for style in _style_chain(paragraph.style):
        name = (style.name or "").strip()
        if name.lower() == "title":
            return 1
        match = _HEADING_STYLE.match(name)
        if match:
            return min(max(int(match.group(1)), 1), 6)
        level = _outline_level(style.element.pPr)
        if level:
            return min(level, 6)
    return None


def _num_pr(paragraph):
    ppr = paragraph._p.pPr
    if ppr is not None and ppr.numPr is not None:
        return ppr.numPr
    for style in _style_chain(paragraph.style):
        spr = style.element.pPr
        if spr is not None and spr.numPr is not None:
            return spr.numPr
    return None


def list_info(paragraph, numbering) -> tuple[int, bool] | None:
    """``(level, ordered)`` for a list paragraph, else ``None``."""
    num_pr = _num_pr(paragraph)
    if num_pr is None or num_pr.numId is None:
        return None
    num_id = num_pr.numId.val
    if num_id == 0:  # numbering explicitly removed
        return None
    ilvl = num_pr.ilvl.val if num_pr.ilvl is not None else 0
    if ilvl == 0:
        # List Bullet 2 / List Number 2 express the level through the style name
        match = re.search(r"(\d)$", paragraph.style.name or "")
        if match and paragraph._p.pPr is not None and paragraph._p.pPr.numPr is None:
            ilvl = max(0, int(match.group(1)) - 1)
    ordered = False
    if numbering is not None:
        fmts = numbering.xpath(
            f'./w:abstractNum[@w:abstractNumId=string(../w:num[@w:numId="{num_id}"]'
            f'/w:abstractNumId/@w:val)]/w:lvl[@w:ilvl="{ilvl}"]/w:numFmt/@w:val')
        if not fmts:
            fmts = numbering.xpath(
                f'./w:abstractNum[@w:abstractNumId=string(../w:num[@w:numId="{num_id}"]'
                f'/w:abstractNumId/@w:val)]/w:lvl[@w:ilvl="0"]/w:numFmt/@w:val')
        ordered = bool(fmts) and fmts[0] in _ORDERED_FORMATS
    if numbering is None and "number" in (paragraph.style.name or "").lower():
        ordered = True
    return ilvl, ordered


def paragraph_text(paragraph) -> str:
    return collapse_spaces(normalize_text(paragraph.text).replace("\n", " "))


def is_all_bold(paragraph) -> bool:
    runs = [r for r in paragraph.runs if r.text.strip()]
    return bool(runs) and all(r.bold or (r.style is not None and r.style.font.bold)
                              for r in runs)


class DocxAdapter:
    id = "docx"
    name = "Word (DOCX)"
    description = ("Tài liệu Word: tiêu đề theo style/outline level, đoạn văn, danh sách, "
                   "bảng và hình ảnh nhúng.")
    file_extensions = (".docx",)

    def can_handle(self, source: SourceRef) -> bool:
        return source.suffix in self.file_extensions or (source.mime or "") == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": TITLE_FIELD,
                "language": LANGUAGE_FIELD,
                "section_level": {
                    "type": "integer", "title": "Cấp tiêu đề để tách phần",
                    "enum": [1, 2, 3], "x-enumLabels": ["H1", "H1–H2", "H1–H3"], "default": 2,
                },
                "bold_as_heading": {
                    "type": "boolean", "title": "Đoạn ngắn in đậm là tiêu đề",
                    "description": "Cho tài liệu không dùng style Heading.", "default": True,
                },
                "extract_images": {"type": "boolean", "title": "Lấy hình ảnh nhúng",
                                   "default": True},
            },
            "additionalProperties": False,
        }

    async def ingest(self, source: SourceRef, ctx: JobContext) -> ContentIR:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        cfg = apply_defaults(self.config_schema(), ctx.config)
        if source.path is None:
            raise ValueError("DOCX cần một file")
        ctx.report_progress(5, "Đọc file Word")
        try:
            doc = Document(str(source.path))
        except Exception as exc:  # noqa: BLE001 - python-docx raises many types
            raise ValueError(f"Không đọc được file Word: {exc}") from exc
        numbering = None
        try:
            numbering = doc.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            numbering = None
        media_dir = source.path.parent / "media"
        builder = _Builder(int(cfg["section_level"]))
        items = list(doc.iter_inner_content())
        n = max(1, len(items))
        image_no = 0
        pending_list: ListBlock | None = None
        list_stack: list[ListItem] = []
        title_from_doc: str | None = None

        def flush_list() -> None:
            nonlocal pending_list, list_stack
            if pending_list is not None:
                builder.add(pending_list)
            pending_list, list_stack = None, []

        for idx, item in enumerate(items):
            ctx.check_cancelled()
            if idx % 20 == 0:
                ctx.report_progress(10 + 70 * idx / n, "Phân tích cấu trúc")
            if isinstance(item, Table):
                flush_list()
                builder.add(table_block(item))
                continue
            assert isinstance(item, Paragraph)
            text = paragraph_text(item)
            images = []
            if cfg["extract_images"]:
                for blob, ext, alt in paragraph_images(item, doc.part):
                    image_no += 1
                    media_dir.mkdir(parents=True, exist_ok=True)
                    name = f"image{image_no}{ext}"
                    (media_dir / name).write_bytes(blob)
                    images.append(ImageBlock(src=f"media/{name}", alt=alt))
            style_name = (item.style.name or "").strip().lower() if item.style else ""
            if style_name in _CAPTION_STYLES and text and builder.last_image is not None:
                builder.last_image.caption = text
                continue
            level = heading_level(item)
            info = list_info(item, numbering) if text and level is None else None
            if info is not None:
                ilvl, ordered = info
                if pending_list is None or (ilvl == 0 and pending_list.ordered != ordered):
                    flush_list()
                    pending_list = ListBlock(ordered=ordered)
                entry = ListItem(text=text)
                parent_level = min(ilvl, len(list_stack))
                if parent_level == 0 or not list_stack:
                    pending_list.items.append(entry)
                    list_stack = [entry]
                else:
                    list_stack[parent_level - 1].items.append(entry)
                    list_stack = list_stack[:parent_level] + [entry]
            else:
                flush_list()
                if text:
                    if level is None and cfg["bold_as_heading"] and is_all_bold(item) and \
                            len(text.split()) <= 12 and text[-1] not in ".,;:!?…":
                        level = 3
                    if level is not None:
                        if style_name == "title" and title_from_doc is None:
                            title_from_doc = text
                        builder.heading(text, level)
                    else:
                        builder.add(ParagraphBlock(text=text))
            for image in images:
                builder.add(image)
        flush_list()
        ctx.report_progress(85, "Đã dựng Content IR")

        props_title = (doc.core_properties.title or "").strip()
        title = (cfg.get("title") or props_title or title_from_doc or builder.first_h1
                 or file_stem_title(source) or "Tài liệu Word")
        words = word_count(" ".join(
            b.text for s in builder.sections for b in s.blocks
            if isinstance(b, ParagraphBlock | HeadingBlock)))
        return ContentIR(
            title=normalize_text(title),
            source=IRSource(type="docx", uri=source.uri, lang=cfg["language"]),
            metadata={"adapter": self.id, "word_count": words,
                      "estimated_duration_s": estimate_duration_s(words),
                      "images": image_no},
            sections=builder.sections,
        )


def table_block(table) -> TableBlock:
    rows: list[list[str]] = []
    for row in table.rows:
        seen: set[int] = set()
        cells = []
        for cell in row.cells:
            if id(cell._tc) in seen:  # horizontally merged cells repeat
                continue
            seen.add(id(cell._tc))
            cells.append(collapse_spaces(normalize_text(cell.text).replace("\n", " ")))
        if any(cells):
            rows.append(cells)
    if not rows:
        return TableBlock()
    return TableBlock(header=rows[0], rows=rows[1:])


def paragraph_images(paragraph, part) -> list[tuple[bytes, str, str]]:
    out = []
    p = paragraph._p
    blips = p.xpath(".//a:blip/@r:embed") + list(p.xpath(
        ".//*[local-name()='imagedata']/@*[local-name()='id']"))
    alts = p.xpath(".//wp:docPr/@descr") or p.xpath(".//wp:docPr/@title")
    for n, rid in enumerate(blips):
        related = part.related_parts.get(rid)
        if related is None or not hasattr(related, "blob"):
            continue
        ctype = getattr(related, "content_type", "")
        ext = IMAGE_EXTS.get(ctype) or Path(str(getattr(related, "partname", ""))).suffix.lower()
        blob = related.blob
        if ext in (".tif", ".tiff"):
            blob, ext = _to_png(blob), ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp") or not blob:
            continue  # EMF/WMF vector drawings are skipped
        out.append((blob, ext, normalize_text(alts[n] if n < len(alts) else "")))
    return out


def _to_png(blob: bytes) -> bytes:
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(blob)) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "PNG")
            return buf.getvalue()
    except Exception:  # noqa: BLE001
        return b""


class _Builder:
    def __init__(self, section_level: int):
        self.section_level = section_level
        self.sections: list[Section] = []
        self.first_h1: str | None = None
        self.last_image: ImageBlock | None = None

    def _section(self) -> Section:
        if not self.sections:
            self.sections.append(Section(id="s1", title="", level=1))
        return self.sections[-1]

    def heading(self, text: str, level: int) -> None:
        if level == 1 and self.first_h1 is None:
            self.first_h1 = text
        if level <= self.section_level:
            if self.sections and not self.sections[-1].blocks and not self.sections[-1].title:
                self.sections.pop()
            self.sections.append(Section(id=f"s{len(self.sections) + 1}", title=text,
                                         level=level))
        self.add(HeadingBlock(text=text, level=level))

    def add(self, block: Block) -> None:
        section = self._section()
        block.id = f"{section.id}-b{len(section.blocks) + 1}"
        section.blocks.append(block)
        self.last_image = block if isinstance(block, ImageBlock) else None
