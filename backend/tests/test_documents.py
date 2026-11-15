"""DOCX / PDF adapters, OCR providers and multi-file (folder / zip) sources."""

from __future__ import annotations

import io
import os
import unicodedata
import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from videostudio.ir import HeadingBlock, ImageBlock, ListBlock, ParagraphBlock, TableBlock
from videostudio.plugins import JobContext, SourceRef
from videostudio.plugins.adapters.docx import DocxAdapter
from videostudio.plugins.adapters.pdf import PdfAdapter
from videostudio.providers.base import ProviderContext, ResolvedProvider
from videostudio.providers.errors import ProviderUnavailableError
from videostudio.providers.ocr import (
    TESSDATA_URL,
    AzureVisionOCR,
    GoogleVisionOCR,
    MockOCR,
    TesseractOCR,
    find_tesseract,
    parse_tesseract_tsv,
)

from .fixtures.make_fixtures import (
    DOCX_NAME,
    SCAN_LINES,
    SCAN_NAME,
    make_all,
    sample_png,
)
from .test_api import wait_job

HAVE_TESSERACT = find_tesseract() is not None
requires_tesseract = pytest.mark.skipif(
    not HAVE_TESSERACT and os.environ.get("VIDEOSTUDIO_REQUIRE_TESSERACT") != "1",
    reason="tesseract not installed")


@pytest.fixture(scope="session")
def docs(tmp_path_factory) -> dict[str, Path]:
    return make_all(tmp_path_factory.mktemp("tài liệu"))


@pytest.fixture(scope="session")
def models_dir(tmp_path_factory) -> Path:
    # Shared across tests so the Vietnamese data is downloaded once per session.
    return tmp_path_factory.mktemp("models")


def copy_ref(src: Path, dest_dir: Path) -> SourceRef:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    dest.write_bytes(src.read_bytes())
    return SourceRef(type="file", uri=dest.name, path=dest, filename=dest.name)


def ctx_with(provider, config: dict | None = None, models: Path | None = None,
             adapter_cfg: dict | None = None) -> JobContext:
    logs: list[str] = []

    def resolve(cap, iid):
        assert cap == "ocr"
        return ResolvedProvider(provider=provider,
                                ctx=ProviderContext(config=config or {}, models_dir=models),
                                instance_id=iid, instance_name=provider.name)

    c = JobContext(job_id="t", project_dir=None, config=adapter_cfg or {},
                   resolve_provider=resolve, log_cb=lambda m, lvl: logs.append(m))
    c.logs = logs  # type: ignore[attr-defined]
    return c


def all_blocks(ir):
    return [b for s in ir.sections for b in s.blocks]


# ----------------------------------------------------------------------------- DOCX


async def test_docx_structure_lists_table_image(docs, tmp_path):
    ref = copy_ref(docs["docx"], tmp_path / "src")
    ir = await DocxAdapter().ingest(ref, JobContext(job_id="t", project_dir=None))
    assert ir.title == "Hướng dẫn trồng cà phê"
    assert ir.source.type == "docx"
    titles = [s.title for s in ir.sections]
    # Heading 1/2 styles, plus a custom style that is a heading only via outline level 2
    assert titles == ["Hướng dẫn trồng cà phê", "Giới thiệu", "Điều kiện sinh trưởng",
                      "Sản lượng theo vùng", "Hình ảnh"]
    blocks = all_blocks(ir)
    lists = [b for b in blocks if isinstance(b, ListBlock)]
    assert [lst.ordered for lst in lists] == [False, True]
    assert [i.text for i in lists[0].items] == ["Đất đỏ bazan giàu dinh dưỡng",
                                                "Lượng mưa từ 1.500 đến 2.000 mm mỗi năm"]
    assert lists[0].items[1].items[0].text == "Nhiệt độ trung bình khoảng 22 độ"
    table = next(b for b in blocks if isinstance(b, TableBlock))
    assert table.header == ["Vùng", "Diện tích (nghìn ha)", "Sản lượng (tấn)"]
    assert table.rows[0] == ["Đắk Lắk", "210", "550.000"]
    image = next(b for b in blocks if isinstance(b, ImageBlock))
    assert image.src == "media/image1.png"
    assert image.alt == "Hạt cà phê Robusta"
    assert image.caption == "Hình 1: Hạt cà phê Robusta Tây Nguyên"
    assert (ref.path.parent / "media" / "image1.png").read_bytes()[:4] == b"\x89PNG"
    # a short paragraph that is bold from start to end reads as a (level 3) heading
    bold = next(b for b in blocks if isinstance(b, HeadingBlock) and b.text == "Lưu ý quan trọng")
    assert bold.level == 3
    assert all(unicodedata.is_normalized("NFC", b.text) for b in blocks
               if isinstance(b, ParagraphBlock))
    assert ir.metadata["images"] == 1


async def test_docx_bold_heading_option_and_bad_file(docs, tmp_path):
    ref = copy_ref(docs["docx"], tmp_path / "a")
    ctx = JobContext(job_id="t", project_dir=None, config={"bold_as_heading": False,
                                                           "extract_images": False})
    ir = await DocxAdapter().ingest(ref, ctx)
    texts = [b.text for b in all_blocks(ir) if isinstance(b, ParagraphBlock)]
    assert "Lưu ý quan trọng" in texts
    assert not any(isinstance(b, ImageBlock) for b in all_blocks(ir))
    bad = tmp_path / "hỏng.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="Không đọc được file Word"):
        await DocxAdapter().ingest(SourceRef(type="file", uri=bad.name, path=bad,
                                             filename=bad.name), ctx)


# ----------------------------------------------------------------------------- PDF (text)


async def test_pdf_text_headings_lists_tables_images(docs, tmp_path):
    ref = copy_ref(docs["pdf"], tmp_path / "src")
    ir = await PdfAdapter().ingest(ref, ctx_with(MockOCR()))
    assert ir.title == "Du lịch Việt Nam"
    assert ir.metadata["pages"] == 2 and "ocr_pages" not in ir.metadata
    assert [(s.title, s.level) for s in ir.sections] == [
        ("Du lịch Việt Nam", 1), ("Vịnh Hạ Long", 2), ("Phố cổ Hội An", 2),
        ("Chi phí tham khảo", 2)]
    blocks = all_blocks(ir)
    lst = next(b for b in blocks if isinstance(b, ListBlock))
    assert [i.text for i in lst.items] == ["Hang Sửng Sốt rộng và đẹp",
                                           "Đảo Ti Tốp có bãi tắm trong xanh"]
    table = next(b for b in blocks if isinstance(b, TableBlock))
    assert table.header == ["Địa điểm", "Vé tham quan", "Thời gian"]
    assert table.rows == [["Hạ Long", "290.000 đ", "1 ngày"], ["Hội An", "120.000 đ", "2 ngày"]]
    image = next(b for b in blocks if isinstance(b, ImageBlock))
    assert (ref.path.parent / image.src).is_file()
    paragraphs = [b.text for b in blocks if isinstance(b, ParagraphBlock)]
    assert paragraphs[0].startswith("Vịnh Hạ Long là di sản thiên nhiên thế giới")
    # page numbers in the footer are dropped, table cells are not repeated as paragraphs
    assert "1" not in paragraphs and "2" not in paragraphs
    assert not any("290.000" in p for p in paragraphs)


async def test_pdf_scanned_page_uses_ocr_provider(docs, tmp_path):
    ref = copy_ref(docs["scan"], tmp_path / "src")
    ctx = ctx_with(MockOCR())
    ir = await PdfAdapter().ingest(ref, ctx)
    assert ir.metadata["ocr_pages"] == [1] and ir.metadata["ocr_engine"] == "mock_ocr"
    text = " ".join(b.text for b in all_blocks(ir) if hasattr(b, "text"))
    assert "Hà Nội, Đà Nẵng, Huế" in text
    assert not list((ref.path.parent / "ocr").glob("*.png"))  # page renders are cleaned up


async def test_pdf_scan_without_ocr_or_failing_ocr(docs, tmp_path):
    ref = copy_ref(docs["scan"], tmp_path / "src")
    with pytest.raises(ValueError, match="trang scan"):
        await PdfAdapter().ingest(ref, ctx_with(MockOCR(), adapter_cfg={"ocr_mode": "never"}))

    class Broken(MockOCR):
        async def recognize(self, ctx, image_path, *, lang):
            raise ProviderUnavailableError("máy OCR hỏng")

    with pytest.raises(ValueError, match="OCR thất bại: máy OCR hỏng"):
        await PdfAdapter().ingest(ref, ctx_with(Broken()))


async def test_pdf_force_ocr_on_text_pdf(docs, tmp_path):
    ref = copy_ref(docs["pdf"], tmp_path / "src")
    ir = await PdfAdapter().ingest(ref, ctx_with(MockOCR(), adapter_cfg={"ocr_mode": "always"}))
    assert ir.metadata["ocr_pages"] == [1, 2]


@requires_tesseract
async def test_pdf_scan_real_tesseract_vietnamese(docs, tmp_path, models_dir):
    """Real OCR: the Vietnamese model is downloaded on demand and every diacritic of the
    scanned page must come back exactly."""
    ref = copy_ref(docs["scan"], tmp_path / "Thư mục có dấu")
    ctx = ctx_with(TesseractOCR(), {"variant": "fast", "psm": 3, "timeout_s": 300}, models_dir)
    ir = await PdfAdapter().ingest(ref, ctx)
    assert ir.metadata["ocr_engine"] == "tesseract"
    assert (models_dir / "tessdata" / "fast" / "vie.traineddata").is_file()
    assert ir.title == "Văn hoá ẩm thực Việt Nam"
    headings = [b.text for b in all_blocks(ir) if isinstance(b, HeadingBlock)]
    assert headings == ["Văn hoá ẩm thực Việt Nam", "Đặc sản miền Trung"]
    body = " ".join(b.text for b in all_blocks(ir) if isinstance(b, ParagraphBlock))
    for kind, line in SCAN_LINES:
        if kind == "body":
            assert line in body, (line, body)


# ----------------------------------------------------------------------------- OCR units


def test_parse_tesseract_tsv_groups_lines_and_drops_noise():
    header = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\t"
              "height\tconf\ttext\n")
    rows = [
        "5\t1\t1\t1\t1\t1\t10\t10\t50\t20\t96\tXin",
        "5\t1\t1\t1\t1\t2\t70\t12\t60\t22\t90\tchào",
        "5\t1\t2\t1\t1\t1\t10\t60\t40\t10\t20\t'",
        "5\t1\t3\t1\t1\t1\t10\t90\t80\t18\t95\tHà",
    ]
    lines = parse_tesseract_tsv(header + "\n".join(rows))
    assert [ln.text for ln in lines] == ["Xin chào", "Hà"]
    assert lines[0].bbox == (10, 10, 130, 34) and lines[0].confidence == 0.93
    assert lines[0].block != lines[1].block


@respx.mock
async def test_tesseract_language_download(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEOSTUDIO_TESSERACT_DIR", raising=False)
    provider = TesseractOCR()
    ctx = ProviderContext(config={"variant": "fast"}, models_dir=tmp_path)
    url = TESSDATA_URL.format(variant="fast", lang="vie")
    route = respx.get(url).mock(return_value=httpx.Response(200, content=b"x" * 200_000))
    assert await provider.ensure_languages(ctx, ["vie"]) == ["vie"]
    assert (tmp_path / "tessdata" / "fast" / "vie.traineddata").stat().st_size == 200_000
    assert await provider.ensure_languages(ctx, ["vie"]) == []  # cached
    assert route.call_count == 1
    respx.get(TESSDATA_URL.format(variant="fast", lang="eng")).mock(
        return_value=httpx.Response(200, content=b"tiny"))
    with pytest.raises(ProviderUnavailableError, match="không hợp lệ"):
        await provider.ensure_languages(ctx, ["eng"])
    assert not (tmp_path / "tessdata" / "fast" / "eng.part").exists()
    assert provider.languages(ProviderContext(config={"extra_langs": "eng, x;y"}), "vi") == [
        "vie", "eng"]


async def test_tesseract_uses_bundled_fast_models(tmp_path, monkeypatch):
    bundle = tmp_path / "tesseract"
    (bundle / "tessdata").mkdir(parents=True)
    (bundle / "tessdata" / "vie.traineddata").write_bytes(b"v" * 150_000)
    monkeypatch.setenv("VIDEOSTUDIO_TESSERACT_DIR", str(bundle))
    provider = TesseractOCR()
    models = tmp_path / "models"
    with respx.mock(assert_all_mocked=True) as router:
        # Every unmocked request fails, so the fast model must come from the bundle.
        await provider.ensure_languages(ProviderContext(config={"variant": "fast"},
                                                        models_dir=models), ["vie"])
        assert (models / "tessdata" / "fast" / "vie.traineddata").read_bytes() == \
            b"v" * 150_000
        best = router.get(TESSDATA_URL.format(variant="best", lang="vie")).mock(
            return_value=httpx.Response(200, content=b"b" * 200_000))
        await provider.ensure_languages(ProviderContext(config={"variant": "best"},
                                                        models_dir=models), ["vie"])
        assert best.call_count == 1  # the bundle only holds the fast models


def _png_file(tmp_path: Path) -> Path:
    p = tmp_path / "trang.png"
    p.write_bytes(sample_png((200, 100)))
    return p


@respx.mock
async def test_google_vision_ocr(tmp_path):
    def sym(t, brk=None):
        s = {"text": t}
        if brk:
            s["property"] = {"detectedBreak": {"type": brk}}
        return s

    word = lambda syms, x: {  # noqa: E731
        "boundingBox": {"vertices": [{"x": x, "y": 5}, {"x": x + 30, "y": 25}]},
        "confidence": 0.9, "symbols": syms}
    body = {"responses": [{"fullTextAnnotation": {"pages": [{"blocks": [{"paragraphs": [{
        "words": [word([sym("H"), sym("à", "SPACE")], 0),
                  word([sym("N"), sym("ộ"), sym("i", "EOL_SURE_SPACE")], 40),
                  word([sym("H"), sym("u"), sym("ế", "LINE_BREAK")], 0)]}]}]}]}}]}
    route = respx.post(GoogleVisionOCR.base_url).mock(return_value=httpx.Response(200,
                                                                                  json=body))
    ctx = ProviderContext(config={"api_key": "AIzaSyTEST1234567890"})
    result = await GoogleVisionOCR().recognize(ctx, _png_file(tmp_path), lang="vi")
    assert [ln.text for ln in result.lines] == ["Hà Nội", "Huế"]
    sent = route.calls[0].request
    assert sent.url.params["key"] == "AIzaSyTEST1234567890"
    assert b'"languageHints":["vi"]' in sent.content.replace(b" ", b"")
    respx.post(GoogleVisionOCR.base_url).mock(return_value=httpx.Response(
        403, json={"error": {"message": "API key not valid AIzaSyTEST1234567890"}}))
    res = await GoogleVisionOCR().test_connection(ctx)
    assert not res.ok and "AIzaSyTEST1234567890" not in res.message


@respx.mock
async def test_azure_vision_ocr(tmp_path):
    body = {"readResult": {"blocks": [{"lines": [
        {"text": "Đà Lạt mộng mơ", "boundingPolygon": [{"x": 1, "y": 2}, {"x": 90, "y": 2},
                                                      {"x": 90, "y": 30}, {"x": 1, "y": 30}],
         "words": [{"confidence": 0.98}, {"confidence": 0.9}]}]}]}}
    route = respx.post("https://vs.cognitiveservices.azure.com/computervision/"
                       "imageanalysis:analyze").mock(return_value=httpx.Response(200, json=body))
    ctx = ProviderContext(config={"endpoint": "https://vs.cognitiveservices.azure.com/",
                                  "api_key": "k" * 32})
    result = await AzureVisionOCR().recognize(ctx, _png_file(tmp_path), lang="vi")
    assert result.lines[0].text == "Đà Lạt mộng mơ" and result.lines[0].height == 28
    assert route.calls[0].request.headers["Ocp-Apim-Subscription-Key"] == "k" * 32
    bad = ProviderContext(config={"endpoint": "http://x", "api_key": "k"})
    assert not (await AzureVisionOCR().test_connection(bad)).ok


# ----------------------------------------------------------------------------- API


def _project(client, auth) -> str:
    return client.post("/projects", headers=auth, json={"name": "Tài liệu"}).json()["id"]


def _ingest(client, auth, pid) -> list[dict]:
    job = client.post(f"/projects/{pid}/ingest", headers=auth, json={}).json()
    done = wait_job(client, auth, job["id"], timeout=120)
    assert done["status"] == "succeeded", done
    return done["result"]["documents"]


def test_api_docx_and_scanned_pdf_with_mock_ocr(client: TestClient, auth, docs):
    pid = _project(client, auth)
    ocr = client.get("/providers?capability=ocr", headers=auth).json()["instances"]
    assert {i["type"] for i in ocr} == {"tesseract", "mock_ocr"}
    assert next(i for i in ocr if i["type"] == "tesseract")["is_default"]
    mock_id = next(i["id"] for i in ocr if i["type"] == "mock_ocr")
    with docs["docx"].open("rb") as fh:
        r = client.post(f"/projects/{pid}/sources", headers=auth,
                        files={"file": (DOCX_NAME, fh, "application/octet-stream")})
    assert r.status_code == 201 and r.json()["adapter_id"] == "docx"
    with docs["scan"].open("rb") as fh:
        r = client.post(f"/projects/{pid}/sources", headers=auth,
                        data={"config": f'{{"ocr_instance_id": "{mock_id}"}}'},
                        files={"file": (SCAN_NAME, fh, "application/pdf")})
    assert r.status_code == 201 and r.json()["adapter_id"] == "pdf"
    docs_out = _ingest(client, auth, pid)
    assert [d["title"] for d in docs_out] == ["Hướng dẫn trồng cà phê", "bản scan"]
    assert docs_out[1]["stats"]["paragraph"] >= 1
    # the storyboard picks up the DOCX image (copied next to the source in the project)
    job = client.post(f"/projects/{pid}/storyboards", headers=auth, json={
        "document_id": docs_out[0]["id"], "settings": {"use_as_is": True}}).json()
    done = wait_job(client, auth, job["id"], timeout=30)
    sb = client.get(f"/storyboards/{done['result']['storyboard_id']}", headers=auth).json()
    images = [s["visual"]["image"] for s in sb["data"]["scenes"] if s["visual"]["type"] == "image"]
    assert images and images[0].startswith(f"projects/{pid}/sources/")
    assert images[0].endswith("/media/image1.png")


class _RawNameInfo(zipfile.ZipInfo):
    """Writes the name as UTF-8 bytes *without* the UTF-8 flag, like many Windows tools."""

    __slots__ = ("raw",)

    def _encodeFilenameFlags(self):  # noqa: N802 - zipfile API
        return self.raw, self.flag_bits & ~0x800


def _zip_bytes(entries: dict[str, bytes], utf8: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name) if utf8 else _RawNameInfo(name)
            if not utf8:
                info.raw = name.encode("utf-8")
            zf.writestr(info, data)
    return buf.getvalue()


MD = ("# Bài học có hình\n\n## Phần một\n\nĐoạn văn đầu tiên khá dài để thành một cảnh.\n\n"
      "![Ảnh minh hoạ](<hình ảnh/ảnh 1.png>)\n\nThêm một câu nữa cho cảnh sau.\n")


def test_api_markdown_zip_resolves_relative_images(client: TestClient, auth):
    pid = _project(client, auth)
    data = _zip_bytes({"bai-hoc/bài học.md": MD.encode(),
                       "bai-hoc/hình ảnh/ảnh 1.png": sample_png((320, 180)),
                       "bai-hoc/__MACOSX/._x": b"junk",
                       "../../evil.txt": b"nope"}, utf8=False)
    r = client.post(f"/projects/{pid}/sources", headers=auth,
                    files={"file": ("bài học.zip", data, "application/zip")})
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["adapter_id"] == "markdown" and src["filename"] == "bài học.md"
    assert src["attachments"] == 1 and src["name"] == "bài học"
    docs_out = _ingest(client, auth, pid)
    job = client.post(f"/projects/{pid}/storyboards", headers=auth, json={
        "document_id": docs_out[0]["id"], "settings": {"use_as_is": True}}).json()
    done = wait_job(client, auth, job["id"], timeout=30)
    sb = client.get(f"/storyboards/{done['result']['storyboard_id']}", headers=auth).json()
    images = [s["visual"]["image"] for s in sb["data"]["scenes"] if s["visual"]["type"] == "image"]
    assert images == [f"projects/{pid}/sources/{src['id']}/hình ảnh/ảnh 1.png"]
    assert client.get(next(iter(sb["image_urls"].values()))).status_code == 200


def test_api_folder_upload_with_relative_paths(client: TestClient, auth, settings):
    pid = _project(client, auth)
    files = [("files", ("bài.md", MD.encode(), "text/markdown")),
             ("files", ("ảnh 1.png", sample_png((64, 64)), "image/png"))]
    r = client.post(f"/projects/{pid}/sources", headers=auth, files=files,
                    data={"paths": ["Thư mục/bài.md", "Thư mục/hình ảnh/ảnh 1.png"]})
    assert r.status_code == 201, r.text
    src = r.json()
    folder = settings.project_dir(pid) / "sources" / src["id"]
    assert (folder / "hình ảnh" / "ảnh 1.png").is_file() and (folder / "bài.md").is_file()
    assert src["attachments"] == 1
    bad = client.post(f"/projects/{pid}/sources", headers=auth,
                      files=[("files", ("a.png", b"x", "image/png")),
                             ("files", ("b.png", b"y", "image/png"))])
    assert bad.status_code == 422 and "Không tìm thấy tài liệu" in bad.json()["detail"]
    broken = client.post(f"/projects/{pid}/sources", headers=auth,
                         files={"file": ("x.zip", b"PK-not-really", "application/zip")})
    assert broken.status_code == 422
    # no half-written folders are left behind by rejected uploads
    remaining = list((settings.project_dir(pid) / "sources").iterdir())
    assert [p.name for p in remaining] == [src["id"]]
