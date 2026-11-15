from __future__ import annotations

import shutil
import unicodedata
from pathlib import Path

import pytest

from videostudio.ir import (
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)
from videostudio.plugins import JobCancelled, JobContext, SourceRef
from videostudio.plugins.adapters.markdown import MarkdownAdapter
from videostudio.plugins.adapters.script import ScriptAdapter

from .conftest import FIXTURES

MD_FIXTURE = FIXTURES / "bài học tiếng việt.md"
SCRIPT_FIXTURE = FIXTURES / "kịch bản mẫu.txt"


def file_ref(path: Path) -> SourceRef:
    return SourceRef(type="file", uri=path.name, path=path, filename=path.name)


def text_ref(text: str, tmp_path: Path) -> SourceRef:
    p = tmp_path / "pasted.txt"
    p.write_text(text, encoding="utf-8")
    return SourceRef(type="text", uri="inline:pasted", path=p, filename=p.name)


# --------------------------------------------------------------------------- script


async def test_script_sections_and_paragraphs(ctx: JobContext) -> None:
    ir = await ScriptAdapter().ingest(file_ref(SCRIPT_FIXTURE), ctx)
    assert [s.title for s in ir.sections] == [
        "Cảnh 1: Giới thiệu",
        "Cảnh 2: Nội dung chính",
        "Cảnh 3: Kết thúc",
    ]
    first = ir.sections[0].blocks
    assert all(isinstance(b, ParagraphBlock) for b in first)
    # Consecutive lines are joined; blank lines split paragraphs.
    assert first[0].text == (
        "Xin chào các bạn, mình là Tú. Hôm nay mình sẽ hướng dẫn các bạn làm video tự động."
    )
    assert first[1].text == "Video này dành cho người mới bắt đầu."
    assert ir.title == "kịch bản mẫu"
    assert ir.source.type == "script"
    assert ir.source.lang == "vi"
    assert ir.metadata["word_count"] > 40
    assert ir.metadata["estimated_duration_s"] > 0
    assert ctx.progress_values[-1] == 80  # type: ignore[attr-defined]


async def test_script_line_mode_and_no_sections(tmp_path: Path) -> None:
    ctx = JobContext(job_id="t", project_dir=tmp_path,
                     config={"split_mode": "line", "detect_sections": False, "title": "Tựa đề"})
    source = text_ref("Dòng một\nDòng hai\n\nCảnh 2 vẫn là đoạn", tmp_path)
    ir = await ScriptAdapter().ingest(source, ctx)
    assert ir.title == "Tựa đề"
    assert len(ir.sections) == 1
    assert [b.text for b in ir.sections[0].blocks] == ["Dòng một", "Dòng hai", "Cảnh 2 vẫn là đoạn"]


async def test_script_pasted_title_from_first_line(ctx: JobContext, tmp_path: Path) -> None:
    ir = await ScriptAdapter().ingest(text_ref("Tiêu đề đầu tiên\n\nNội dung.", tmp_path), ctx)
    assert ir.title == "Tiêu đề đầu tiên"


async def test_script_normalizes_nfd_to_nfc(ctx: JobContext, tmp_path: Path) -> None:
    nfd = unicodedata.normalize("NFD", "Tiếng Việt có dấu")
    assert nfd != "Tiếng Việt có dấu"
    ir = await ScriptAdapter().ingest(text_ref(nfd, tmp_path), ctx)
    assert ir.sections[0].blocks[0].text == "Tiếng Việt có dấu"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp1258"])
async def test_script_decodes_common_encodings(encoding: str, ctx: JobContext,
                                               tmp_path: Path) -> None:
    text = "Xin chào thế giới"
    if encoding == "cp1258":
        # Only characters that are precomposed in cp1258 (others need combining marks).
        text = "Xin chào các anh, đây là món ăn"
    path = tmp_path / f"script {encoding}.txt"
    path.write_bytes(text.encode(encoding))
    ir = await ScriptAdapter().ingest(file_ref(path), ctx)
    assert ir.sections[0].blocks[0].text == text


async def test_script_respects_cancellation(tmp_path: Path) -> None:
    ctx = JobContext(job_id="t", project_dir=tmp_path, cancelled_cb=lambda: True)
    with pytest.raises(JobCancelled):
        await ScriptAdapter().ingest(text_ref("abc", tmp_path), ctx)


def test_script_can_handle() -> None:
    a = ScriptAdapter()
    assert a.can_handle(SourceRef(type="text", uri="inline:"))
    assert a.can_handle(SourceRef(type="file", uri="a.txt", filename="a.TXT"))
    assert not a.can_handle(SourceRef(type="file", uri="a.md", filename="a.md"))


# --------------------------------------------------------------------------- markdown


@pytest.fixture
async def md_ir(ctx: JobContext):
    return await MarkdownAdapter().ingest(file_ref(MD_FIXTURE), ctx)


async def test_markdown_title_and_front_matter(md_ir) -> None:
    assert md_ir.title == "Hướng dẫn làm video tự động"
    fm = md_ir.metadata["front_matter"]
    assert fm["author"] == "Phạm Văn Tú"
    assert fm["tags"] == ["video", "tự động hoá"]
    assert md_ir.source.lang == "vi"
    assert md_ir.source.type == "markdown"


async def test_markdown_sections(md_ir) -> None:
    assert [(s.title, s.level) for s in md_ir.sections] == [
        ("Hướng dẫn làm video tự động", 1),
        ("Phần 1: Chuẩn bị", 2),
        ("Phần 2: So sánh định dạng", 2),
        ("Phần 3: Ví dụ mã nguồn", 2),
    ]
    ids = [b.id for s in md_ir.sections for b in s.blocks]
    assert len(ids) == len(set(ids))


async def test_markdown_paragraphs_and_images(md_ir) -> None:
    intro = md_ir.sections[0].blocks
    assert isinstance(intro[0], HeadingBlock) and intro[0].level == 1
    assert isinstance(intro[1], ParagraphBlock)
    assert intro[1].text == (
        "Xin chào các bạn! Hôm nay chúng ta sẽ tìm hiểu cách biến tài liệu thành video có "
        "thuyết minh và phụ đề bằng tiếng Việt."
    )
    image = intro[2]
    assert isinstance(image, ImageBlock)
    assert image.src == "images/giao-dien.png"
    assert image.alt == "Ảnh minh hoạ giao diện"
    assert image.caption == "Giao diện chính"

    sec2 = md_ir.sections[2].blocks
    inline_img = [b for b in sec2 if isinstance(b, ImageBlock)]
    assert inline_img and inline_img[0].alt == "biểu tượng" and inline_img[0].src == "icon.svg"
    para = [b for b in sec2 if isinstance(b, ParagraphBlock)][0]
    assert para.text == "Đoạn văn có hình ảnh nằm giữa câu vẫn được giữ lại."


async def test_markdown_lists(md_ir) -> None:
    lists = [b for b in md_ir.sections[1].blocks if isinstance(b, ListBlock)]
    assert len(lists) == 2
    bullets, steps = lists
    assert not bullets.ordered
    assert [i.text for i in bullets.items] == [
        "Máy tính chạy Windows 10 hoặc 11",
        "Kết nối mạng ổn định",
        "Tài khoản YouTube đã xác minh",
    ]
    assert [i.text for i in bullets.items[1].items] == [
        "Tốc độ tải lên tối thiểu 5 Mbps",
        "Không dùng VPN khi đăng video",
    ]
    assert steps.ordered
    assert steps.items[1].text == "Chạy file VideoStudio-setup.exe"


async def test_markdown_blockquote_marked(md_ir) -> None:
    quotes = [b for b in md_ir.sections[1].blocks if b.meta.get("quote")]
    assert len(quotes) == 1
    assert quotes[0].text.startswith("Lưu ý:")


async def test_markdown_table(md_ir) -> None:
    table = next(b for b in md_ir.sections[2].blocks if isinstance(b, TableBlock))
    assert table.header == ["Định dạng", "Tỉ lệ khung hình", "Nền tảng phù hợp"]
    assert table.rows == [
        ["Giải thích", "16:9", "YouTube, Facebook"],
        ["Video ngắn", "9:16", "TikTok, Reels, Shorts"],
    ]


async def test_markdown_code(md_ir) -> None:
    code = next(b for b in md_ir.sections[3].blocks if isinstance(b, CodeBlock))
    assert code.language == "python"
    assert code.code == 'print("Xin chào, thế giới!")'


async def test_markdown_options(tmp_path: Path) -> None:
    ctx = JobContext(job_id="t", project_dir=tmp_path,
                     config={"section_level": 1, "include_code": False,
                             "keep_heading_blocks": False})
    ir = await MarkdownAdapter().ingest(file_ref(MD_FIXTURE), ctx)
    assert len(ir.sections) == 1
    kinds = {b.kind for b in ir.iter_blocks()}
    assert "code" not in kinds and "heading" not in kinds


async def test_markdown_path_with_spaces_and_diacritics(tmp_path: Path, ctx: JobContext) -> None:
    target_dir = tmp_path / "Thư mục có dấu cách" / "Tài liệu"
    target_dir.mkdir(parents=True)
    target = target_dir / "Bài viết số 1.md"
    shutil.copy(MD_FIXTURE, target)
    ir = await MarkdownAdapter().ingest(file_ref(target), ctx)
    assert ir.title == "Hướng dẫn làm video tự động"


async def test_markdown_without_h1_uses_filename(tmp_path: Path, ctx: JobContext) -> None:
    p = tmp_path / "ghi_chu_nhanh.md"
    p.write_text("Chỉ có một đoạn văn.\n", encoding="utf-8")
    ir = await MarkdownAdapter().ingest(file_ref(p), ctx)
    assert ir.title == "ghi chu nhanh"
    assert ir.sections[0].blocks[0].text == "Chỉ có một đoạn văn."
