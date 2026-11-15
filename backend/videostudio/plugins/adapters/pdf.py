"""PDF adapter based on PyMuPDF.

Text pages: blocks/lines/spans with font sizes. The body size is the size that covers
the most characters; larger (or short, fully bold) lines become headings, ranked by size.
Tables come from ``page.find_tables()``; embedded images are written to ``media/`` next to
the source. Repeated header/footer lines and page numbers are dropped.

Scanned pages (almost no text but a large image), or every page when OCR is forced, are
rendered to PNG and sent to the configured ``ocr`` provider; line heights then play the
role of font sizes.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
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

_BULLET = re.compile(r"^\s*(?:[•◦▪▫●○■□►▸\-–—*·]|\d{1,2}[.)]|[a-zA-Z][.)])\s+")
_ORDERED = re.compile(r"^\s*(?:\d{1,2}|[a-zA-Z])[.)]\s+")
_PAGE_NO = re.compile(r"^(?:trang|page)?\s*\d{1,4}(?:\s*(?:/|of|trên)\s*\d{1,4})?$", re.I)
MIN_TEXT_CHARS = 25
MIN_IMAGE_PX = 80


@dataclass
class _Item:
    page: int
    y: float
    x: float
    kind: str  # text | table | image
    text: str = ""
    size: float = 0.0
    bold: bool = False
    lines: list[str] = field(default_factory=list)
    block: Block | None = None
    ocr: bool = False
    block_no: int = -1  # OCR paragraph id


def _is_bold(span: dict[str, Any]) -> bool:
    return bool(span.get("flags", 0) & 16) or "bold" in str(span.get("font", "")).lower()


def _join_lines(lines: list[str]) -> str:
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}" if out else line
    return collapse_spaces(out)


def _norm_repeat(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


class PdfAdapter:
    id = "pdf"
    name = "PDF"
    description = ("Tài liệu PDF: tiêu đề theo cỡ chữ, đoạn văn, danh sách, bảng, hình ảnh; "
                   "trang scan được nhận dạng chữ bằng OCR.")
    file_extensions = (".pdf",)

    def can_handle(self, source: SourceRef) -> bool:
        return source.suffix in self.file_extensions or (source.mime or "") == "application/pdf"

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
                "ocr_mode": {
                    "type": "string", "title": "Nhận dạng chữ (OCR)",
                    "enum": ["auto", "always", "never"],
                    "x-enumLabels": ["Tự động cho trang scan", "Mọi trang", "Không dùng"],
                    "default": "auto",
                },
                "ocr_instance_id": {
                    "type": "string", "title": "Nhà cung cấp OCR",
                    "description": "Để trống để dùng nhà cung cấp OCR mặc định.",
                    "default": "",
                },
                "ocr_dpi": {"type": "integer", "title": "Độ phân giải OCR (dpi)",
                            "enum": [200, 300, 400], "default": 300},
                "extract_images": {"type": "boolean", "title": "Lấy hình ảnh", "default": True},
                "detect_tables": {"type": "boolean", "title": "Nhận diện bảng", "default": True},
                "skip_headers_footers": {"type": "boolean",
                                         "title": "Bỏ đầu trang / chân trang / số trang",
                                         "default": True},
            },
            "additionalProperties": False,
        }

    async def ingest(self, source: SourceRef, ctx: JobContext) -> ContentIR:
        import pymupdf

        getattr(pymupdf, "no_recommend_layout", lambda: None)()
        cfg = apply_defaults(self.config_schema(), ctx.config)
        if source.path is None:
            raise ValueError("PDF cần một file")
        ctx.report_progress(3, "Mở file PDF")
        try:
            doc = pymupdf.open(str(source.path))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Không đọc được file PDF: {exc}") from exc
        if doc.needs_pass:
            raise ValueError("File PDF có mật khẩu, hãy bỏ mật khẩu rồi thử lại")
        media_dir = source.path.parent / "media"
        items: list[_Item] = []
        ocr_pages: list[int] = []
        ocr_engine = None
        ocr_error: str | None = None
        seen_images: set[int] = set()
        n_pages = max(1, doc.page_count)
        for pno in range(doc.page_count):
            ctx.check_cancelled()
            page = doc[pno]
            ctx.report_progress(5 + 75 * pno / n_pages, f"Trang {pno + 1}/{n_pages}")
            if self._needs_ocr(page, cfg["ocr_mode"]):
                try:
                    page_items, ocr_engine = await self._ocr_page(page, pno, cfg, ctx,
                                                                  source.path.parent)
                except Exception as exc:  # noqa: BLE001 - reported below
                    ocr_error = getattr(exc, "message", None) or str(exc)
                    ctx.log(f"Trang {pno + 1}: OCR lỗi: {ocr_error}", "warning")
                    page_items = []
                else:
                    ocr_pages.append(pno + 1)
                    ctx.log(f"Trang {pno + 1}: nhận dạng chữ bằng {ocr_engine} "
                            f"({sum(len(i.text) for i in page_items)} ký tự)")
                items.extend(page_items)
                continue
            items.extend(self._text_page(page, pno, cfg, media_dir, seen_images, doc))
        if not any(i.kind == "text" and i.text for i in items):
            if ocr_error:
                raise ValueError(f"PDF không có lớp chữ và OCR thất bại: {ocr_error}")
            raise ValueError("Không tìm thấy chữ trong PDF (trang scan cần bật OCR)")
        if cfg["skip_headers_footers"]:
            items = _drop_repeated(items, doc)
        ctx.report_progress(85, "Phân tích tiêu đề")
        sections, first_h1 = _build_sections(items, int(cfg["section_level"]))
        meta_title = normalize_text((doc.metadata or {}).get("title") or "").strip()
        title = (cfg.get("title") or meta_title or first_h1 or file_stem_title(source)
                 or "Tài liệu PDF")
        words = word_count(" ".join(i.text for i in items if i.kind == "text"))
        metadata: dict[str, Any] = {
            "adapter": self.id, "pages": doc.page_count, "word_count": words,
            "estimated_duration_s": estimate_duration_s(words), "images": len(seen_images),
        }
        if ocr_pages:
            metadata["ocr_pages"] = ocr_pages
            metadata["ocr_engine"] = ocr_engine
        doc.close()
        return ContentIR(title=title, source=IRSource(type="pdf", uri=source.uri,
                                                      lang=cfg["language"]),
                         metadata=metadata, sections=sections)

    # ------------------------------------------------------------------ text pages

    @staticmethod
    def _needs_ocr(page, mode: str) -> bool:
        if mode == "always":
            return True
        if mode == "never":
            return False
        if len(page.get_text("text").strip()) >= MIN_TEXT_CHARS:
            return False
        import pymupdf

        area = abs(page.rect)
        covered = sum(abs(pymupdf.Rect(info["bbox"]) & page.rect)
                      for info in page.get_image_info())
        return area > 0 and covered / area > 0.3

    def _text_page(self, page, pno: int, cfg: dict[str, Any], media_dir: Path,
                   seen_images: set[int], doc) -> list[_Item]:
        import pymupdf

        items: list[_Item] = []
        table_rects = []
        if cfg["detect_tables"]:
            try:
                tables = page.find_tables().tables
            except Exception:  # noqa: BLE001 - table detection is best effort
                tables = []
            for table in tables:
                rows = [[collapse_spaces(normalize_text(c or "").replace("\n", " "))
                         for c in row] for row in table.extract()]
                rows = [r for r in rows if any(r)]
                if len(rows) < 2 or max(len(r) for r in rows) < 2:
                    continue
                rect = pymupdf.Rect(table.bbox)
                table_rects.append(rect)
                items.append(_Item(page=pno, y=rect.y0, x=rect.x0, kind="table",
                                   block=TableBlock(header=rows[0], rows=rows[1:])))
        data = page.get_text("dict", sort=True, flags=pymupdf.TEXTFLAGS_TEXT)
        for blk in data.get("blocks", []):
            if blk.get("type") != 0:
                continue
            rect = pymupdf.Rect(blk["bbox"])
            if any(rect.intersect(t).get_area() > 0.5 * rect.get_area() for t in table_rects
                   if rect.get_area() > 0):
                continue
            groups: list[_Item] = []
            for line in blk.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                text = normalize_text("".join(s["text"] for s in line["spans"]))
                chars = Counter()
                bold_chars = 0
                for s in spans:
                    n = len(s["text"].strip())
                    chars[round(float(s["size"]) * 2) / 2] += n
                    bold_chars += n if _is_bold(s) else 0
                size = chars.most_common(1)[0][0]
                bold = bold_chars >= 0.8 * sum(chars.values())
                y0, x0 = line["bbox"][1], line["bbox"][0]
                prev = groups[-1] if groups else None
                starts_item = bool(_BULLET.match(text))
                if prev and abs(prev.size - size) <= 0.6 and prev.bold == bold and \
                        not starts_item:
                    prev.lines.append(text)
                else:
                    groups.append(_Item(page=pno, y=y0, x=x0, kind="text", size=size,
                                        bold=bold, lines=[text]))
            for g in groups:
                g.text = _join_lines(g.lines)
            items.extend(g for g in groups if g.text)
        if cfg["extract_images"]:
            items.extend(self._images(page, pno, media_dir, seen_images, doc))
        items.sort(key=lambda i: (i.y, i.x))
        return items

    def _images(self, page, pno: int, media_dir: Path, seen: set[int], doc) -> list[_Item]:
        import pymupdf

        out = []
        page_area = abs(page.rect)
        for info in page.get_image_info(xrefs=True):
            xref = info.get("xref") or 0
            if not xref or xref in seen:
                continue
            if info.get("width", 0) < MIN_IMAGE_PX or info.get("height", 0) < MIN_IMAGE_PX:
                continue
            bbox = pymupdf.Rect(info["bbox"])
            if page_area and abs(bbox) / page_area < 0.02:
                continue
            try:
                extracted = doc.extract_image(xref)
                ext = extracted.get("ext", "png")
                data = extracted.get("image", b"")
                if ext not in ("png", "jpg", "jpeg") or extracted.get("smask"):
                    pix = pymupdf.Pixmap(doc, xref)
                    if pix.alpha or pix.n - pix.alpha > 3:
                        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                    data, ext = pix.tobytes("png"), "png"
            except Exception:  # noqa: BLE001 - unusual encodings are skipped
                continue
            seen.add(xref)
            media_dir.mkdir(parents=True, exist_ok=True)
            name = f"p{pno + 1}-{len(seen)}.{ext}"
            (media_dir / name).write_bytes(data)
            out.append(_Item(page=pno, y=bbox.y0, x=bbox.x0, kind="image",
                             block=ImageBlock(src=f"media/{name}",
                                              alt=f"Hình trang {pno + 1}")))
        return out

    # ------------------------------------------------------------------ OCR pages

    async def _ocr_page(self, page, pno: int, cfg: dict[str, Any], ctx: JobContext,
                        workdir: Path) -> tuple[list[_Item], str]:
        resolved = ctx.provider("ocr", cfg.get("ocr_instance_id") or None)
        dpi = int(cfg["ocr_dpi"])
        import pymupdf

        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        ocr_dir = workdir / "ocr"
        ocr_dir.mkdir(parents=True, exist_ok=True)
        png = ocr_dir / f"page-{pno + 1}.png"
        pix.save(str(png))
        try:
            result = await resolved.provider.recognize(resolved.ctx, png, lang=cfg["language"])
        finally:
            png.unlink(missing_ok=True)
        # pixel height → approximate points so mixed text/scan documents compare sizes
        scale = 72.0 / dpi / 0.72
        items: list[_Item] = []
        for line in result.lines:
            text = collapse_spaces(normalize_text(line.text))
            if not text:
                continue
            size = round(line.height * scale * 2) / 2
            y0, x0 = line.bbox[1] * 72 / dpi, line.bbox[0] * 72 / dpi
            prev = items[-1] if items else None
            if prev and prev.block_no == line.block and \
                    abs(prev.size - size) <= max(1.5, 0.2 * prev.size) and \
                    not _BULLET.match(text):
                prev.lines.append(text)
            else:
                items.append(_Item(page=pno, y=y0, x=x0, kind="text", size=size,
                                   lines=[text], ocr=True, block_no=line.block))
        for item in items:
            item.text = _join_lines(item.lines)
        return [i for i in items if i.text], result.engine or resolved.provider.id


def _drop_repeated(items: list[_Item], doc) -> list[_Item]:
    if doc.page_count == 0:
        return items
    heights = {p: doc[p].rect.height for p in range(doc.page_count)}
    margin = {p: 0.08 * h for p, h in heights.items()}

    def in_margin(i: _Item) -> bool:
        h = heights.get(i.page, 0)
        return i.y < margin.get(i.page, 0) or i.y > h - margin.get(i.page, 0)

    counts: Counter[str] = Counter()
    for i in items:
        if i.kind == "text" and in_margin(i):
            counts[_norm_repeat(i.text)] += 1
    threshold = max(2, int(0.5 * doc.page_count + 0.5))
    out = []
    for i in items:
        if i.kind == "text" and in_margin(i) and len(i.text) < 120 and (
                _PAGE_NO.match(i.text.strip()) or
                (doc.page_count > 1 and counts[_norm_repeat(i.text)] >= threshold)):
            continue
        out.append(i)
    return out


def _body_size(items: list[_Item]) -> float:
    chars: Counter[float] = Counter()
    for i in items:
        if i.kind == "text":
            chars[i.size] += len(i.text)
    return chars.most_common(1)[0][0] if chars else 11.0


def _heading_levels(items: list[_Item]) -> dict[int, int]:
    """Map item index → heading level."""
    body = _body_size(items)
    candidates: dict[int, float] = {}
    for idx, i in enumerate(items):
        if i.kind != "text" or not i.text or _BULLET.match(i.text):
            continue
        words = len(i.text.split())
        if words > 20 or len(i.lines) > 3:
            continue
        ratio = 1.25 if i.ocr else 1.15
        if i.size >= body * ratio:
            candidates[idx] = i.size
        elif i.bold and i.size >= body * 0.95 and words <= 12 and \
                i.text[-1] not in ".,;:!?…":
            candidates[idx] = body  # bold body-size line: lowest heading level
    sizes = sorted({s for s in candidates.values() if s > body}, reverse=True)
    # sizes within ~10 % of each other are the same level (OCR heights are noisy)
    levels: list[float] = []
    for s in sizes:
        if not levels or s < levels[-1] * 0.9:
            levels.append(s)
    out = {}
    for idx, size in candidates.items():
        if size <= body:
            out[idx] = min(len(levels) + 1, 3)
        else:
            level = next((n for n, s in enumerate(levels, 1) if size >= s * 0.9), len(levels))
            out[idx] = min(level, 6)
    return out


def _build_sections(items: list[_Item], section_level: int) -> tuple[list[Section], str | None]:
    levels = _heading_levels(items)
    sections: list[Section] = []
    first_h1: str | None = None
    pending: ListBlock | None = None

    def current() -> Section:
        if not sections:
            sections.append(Section(id="s1", title="", level=1))
        return sections[-1]

    def add(block: Block) -> None:
        sec = current()
        block.id = f"{sec.id}-b{len(sec.blocks) + 1}"
        sec.blocks.append(block)

    def flush() -> None:
        nonlocal pending
        if pending is not None and pending.items:
            add(pending)
        pending = None

    for idx, item in enumerate(items):
        if item.kind in ("table", "image"):
            flush()
            assert item.block is not None
            add(item.block)
            continue
        level = levels.get(idx)
        if level is not None:
            flush()
            if level == 1 and first_h1 is None:
                first_h1 = item.text
            if level <= section_level:
                if sections and not sections[-1].blocks and not sections[-1].title:
                    sections.pop()
                sections.append(Section(id=f"s{len(sections) + 1}", title=item.text,
                                        level=level))
            add(HeadingBlock(text=item.text, level=level))
            continue
        bullet = _BULLET.match(item.text)
        if bullet:
            ordered = bool(_ORDERED.match(item.text))
            if pending is None or pending.ordered != ordered:
                flush()
                pending = ListBlock(ordered=ordered)
            pending.items.append(ListItem(text=item.text[bullet.end():].strip()))
            continue
        flush()
        add(ParagraphBlock(text=item.text))
    flush()
    return sections, first_h1
