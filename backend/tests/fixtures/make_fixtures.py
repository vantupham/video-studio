"""Generate the Vietnamese DOCX / PDF fixtures (kept out of git as binaries).

    uv run python tests/fixtures/make_fixtures.py <out_dir>

writes ``tài liệu mẫu.docx``, ``tài liệu mẫu.pdf`` (text layer) and ``bản scan.pdf``
(a rasterized page without any text layer, for OCR).
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

FONTS = Path(__file__).resolve().parents[2] / "videostudio" / "assets" / "fonts"
DOCX_NAME = "tài liệu mẫu.docx"
PDF_NAME = "tài liệu mẫu.pdf"
SCAN_NAME = "bản scan.pdf"

SCAN_LINES = [
    ("title", "Văn hoá ẩm thực Việt Nam"),
    ("body", "Phở là món ăn truyền thống nổi tiếng của người Hà Nội."),
    ("body", "Bánh mì Sài Gòn được du khách khắp thế giới yêu thích."),
    ("heading", "Đặc sản miền Trung"),
    ("body", "Bún bò Huế có vị cay nồng và hương sả đặc trưng."),
    ("body", "Mì Quảng thường được ăn kèm bánh tráng nướng giòn."),
]


def sample_png(size: tuple[int, int] = (480, 300), label: str = "Cà phê") -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", size, (46, 94, 170))
    draw = ImageDraw.Draw(im)
    for i in range(0, size[0], 24):
        draw.line([(i, 0), (i + size[1], size[1])], fill=(70, 120, 200), width=6)
    draw.ellipse([size[0] // 2 - 70, size[1] // 2 - 90, size[0] // 2 + 70, size[1] // 2 + 50],
                 fill=(111, 78, 55))
    font = ImageFont.truetype(str(FONTS / "BeVietnamPro-Bold.ttf"), 34)
    draw.text((size[0] // 2, size[1] - 40), label, font=font, fill="white", anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def make_docx(path: Path) -> Path:
    from docx import Document
    from docx.enum.style import WD_STYLE_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches

    doc = Document()
    doc.core_properties.title = "Hướng dẫn trồng cà phê"
    doc.add_paragraph("Hướng dẫn trồng cà phê", style="Title")
    doc.add_heading("Giới thiệu", level=1)
    doc.add_paragraph("Cà phê là cây công nghiệp quan trọng của Việt Nam. Tây Nguyên là "
                      "vùng trồng cà phê lớn nhất cả nước.")
    doc.add_heading("Điều kiện sinh trưởng", level=2)
    doc.add_paragraph("Cây cà phê cần những điều kiện sau:")
    doc.add_paragraph("Đất đỏ bazan giàu dinh dưỡng", style="List Bullet")
    doc.add_paragraph("Lượng mưa từ 1.500 đến 2.000 mm mỗi năm", style="List Bullet")
    doc.add_paragraph("Nhiệt độ trung bình khoảng 22 độ", style="List Bullet 2")
    doc.add_paragraph("Các bước chăm sóc:")
    doc.add_paragraph("Bón phân hữu cơ đầu mùa mưa", style="List Number")
    doc.add_paragraph("Tỉa cành sau thu hoạch", style="List Number")

    # A custom style that is a heading only through its outline level.
    custom = doc.styles.add_style("Đề mục riêng", WD_STYLE_TYPE.PARAGRAPH)
    ppr = custom.element.get_or_add_pPr()
    outline = OxmlElement("w:outlineLvl")
    outline.set(qn("w:val"), "1")
    ppr.append(outline)
    doc.add_paragraph("Sản lượng theo vùng", style="Đề mục riêng")
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ["Vùng", "Diện tích (nghìn ha)", "Sản lượng (tấn)"],
                          strict=True):
        cell.text = text
    for row in (["Đắk Lắk", "210", "550.000"], ["Lâm Đồng", "175", "480.000"]):
        cells = table.add_row().cells
        for cell, text in zip(cells, row, strict=True):
            cell.text = text

    doc.add_heading("Hình ảnh", level=1)
    pic_par = doc.add_paragraph()
    pic_par.add_run().add_picture(io.BytesIO(sample_png()), width=Inches(3))
    docpr = pic_par._p.xpath(".//wp:docPr")[0]
    docpr.set("descr", "Hạt cà phê Robusta")
    doc.add_paragraph("Hình 1: Hạt cà phê Robusta Tây Nguyên", style="Caption")
    bold = doc.add_paragraph()
    bold.add_run("Lưu ý quan trọng").bold = True
    doc.add_paragraph("Không tưới quá nhiều nước trong mùa ra hoa.")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def _fonts(page) -> None:
    page.insert_font(fontname="bvp", fontfile=str(FONTS / "BeVietnamPro-Regular.ttf"))
    page.insert_font(fontname="bvpb", fontfile=str(FONTS / "BeVietnamPro-Bold.ttf"))


def make_text_pdf(path: Path) -> Path:
    import pymupdf

    doc = pymupdf.open()
    doc.set_metadata({"title": "Du lịch Việt Nam"})
    page = doc.new_page(width=595, height=842)
    _fonts(page)
    page.insert_text((60, 90), "Du lịch Việt Nam", fontname="bvpb", fontsize=24)
    page.insert_text((60, 140), "Vịnh Hạ Long", fontname="bvpb", fontsize=16)
    page.insert_textbox(pymupdf.Rect(60, 150, 535, 230),
                        "Vịnh Hạ Long là di sản thiên nhiên thế giới với gần hai nghìn hòn đảo "
                        "đá vôi. Du khách thường đi thuyền ngắm cảnh và khám phá hang động.",
                        fontname="bvp", fontsize=11)
    page.insert_text((60, 250), "• Hang Sửng Sốt rộng và đẹp", fontname="bvp", fontsize=11)
    page.insert_text((60, 268), "• Đảo Ti Tốp có bãi tắm trong xanh", fontname="bvp",
                     fontsize=11)
    page.insert_image(pymupdf.Rect(60, 290, 300, 440), stream=sample_png(label="Hạ Long"))
    page.insert_text((60, 480), "Phố cổ Hội An", fontname="bvpb", fontsize=16)
    page.insert_textbox(pymupdf.Rect(60, 490, 535, 560),
                        "Hội An nổi tiếng với những dãy nhà cổ, đèn lồng rực rỡ và ẩm thực "
                        "đặc sắc như cao lầu, bánh mì Phượng.",
                        fontname="bvp", fontsize=11)
    page.insert_text((290, 810), "1", fontname="bvp", fontsize=9)

    page = doc.new_page(width=595, height=842)
    _fonts(page)
    page.insert_text((60, 90), "Chi phí tham khảo", fontname="bvpb", fontsize=16)
    rows = [["Địa điểm", "Vé tham quan", "Thời gian"],
            ["Hạ Long", "290.000 đ", "1 ngày"],
            ["Hội An", "120.000 đ", "2 ngày"]]
    x0, y0, col_w, row_h = 60, 110, 160, 26
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            rect = pymupdf.Rect(x0 + c * col_w, y0 + r * row_h, x0 + (c + 1) * col_w,
                                y0 + (r + 1) * row_h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 6, rect.y0 + 17), text,
                             fontname="bvpb" if r == 0 else "bvp", fontsize=10.5)
    page.insert_textbox(pymupdf.Rect(60, 210, 535, 280),
                        "Giá vé có thể thay đổi theo mùa. Nên đặt trước vào dịp lễ Tết.",
                        fontname="bvp", fontsize=11)
    page.insert_text((290, 810), "2", fontname="bvp", fontsize=9)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def document_font() -> Path:
    """A Times-like font as found in typical printed documents (Windows: Times New Roman).
    Be Vietnam Pro's rounded 'a' is misread by Tesseract, which real scans rarely use."""
    candidates = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "times.ttf",
                  Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
                  Path("/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
                  Path("/Library/Fonts/Times New Roman.ttf")]
    return next((p for p in candidates if p.is_file()), FONTS / "BeVietnamPro-Regular.ttf")


def make_scanned_pdf(path: Path, dpi: int = 200) -> Path:
    """A page drawn with real fonts, rasterized, then placed as the only content of a new
    PDF: exactly what a scanner produces (no text layer)."""
    import pymupdf

    src = pymupdf.open()
    page = src.new_page(width=595, height=842)
    page.insert_font(fontname="doc", fontfile=str(document_font()))
    y = 90
    for kind, text in SCAN_LINES:
        size = {"title": 22, "heading": 16, "body": 12}[kind]
        y += {"title": 0, "heading": 22, "body": 8}[kind]
        page.insert_text((60, y), text, fontname="doc", fontsize=size)
        y += size + 10
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    out = pymupdf.open()
    scan = out.new_page(width=595, height=842)
    scan.insert_image(scan.rect, stream=pix.tobytes("png"))
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(path), deflate=True)
    return path


def make_all(out_dir: Path) -> dict[str, Path]:
    return {"docx": make_docx(out_dir / DOCX_NAME), "pdf": make_text_pdf(out_dir / PDF_NAME),
            "scan": make_scanned_pdf(out_dir / SCAN_NAME)}


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    for kind, p in make_all(target).items():
        print(f"{kind}: {p} ({p.stat().st_size} bytes)")
