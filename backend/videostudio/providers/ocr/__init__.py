"""OCR providers: Tesseract (local, Vietnamese data downloaded on demand), Google Cloud
Vision, Azure AI Vision and an offline mock."""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

import httpx

from ..base import (
    OCRLine,
    OCRProvider,
    OCRResult,
    ProviderContext,
    TestResult,
    secret_field,
    timeout_field,
)
from ..errors import ProviderConfigError, ProviderError, ProviderUnavailableError, client, send

EXE = ".exe" if sys.platform == "win32" else ""
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
ENV_TESSERACT_DIR = "VIDEOSTUDIO_TESSERACT_DIR"
TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata_{variant}/raw/main/{lang}.traineddata"
# BCP-47 → Tesseract language codes.
TESS_LANGS = {"vi": "vie", "en": "eng"}
MIN_TRAINEDDATA_BYTES = 100_000
# Download timeout: the 'best' Vietnamese model is ~12 MB.
DOWNLOAD_TIMEOUT_S = 180


def _tesseract_candidates(configured: str | None) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if configured:
        p = Path(configured)
        out.append(("config", p if p.suffix.lower() == EXE or p.is_file()
                    else p / f"tesseract{EXE}"))
    env_dir = os.environ.get(ENV_TESSERACT_DIR)
    if env_dir:
        out.append(("env", Path(env_dir) / f"tesseract{EXE}"))
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        out.append(("bundled", exe_dir.parent / "tesseract" / f"tesseract{EXE}"))
    which = shutil.which("tesseract")
    if which:
        out.append(("path", Path(which)))
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                out.append(("installed", Path(base) / "Tesseract-OCR" / "tesseract.exe"))
                out.append(("installed", Path(base) / "Programs" / "Tesseract-OCR"
                            / "tesseract.exe"))
    return out


def bundled_tessdata_dirs() -> list[Path]:
    """``tessdata`` folders shipped next to a bundled Tesseract (fast models)."""
    out = []
    env_dir = os.environ.get(ENV_TESSERACT_DIR)
    if env_dir:
        out.append(Path(env_dir) / "tessdata")
    if getattr(sys, "frozen", False):
        out.append(Path(sys.executable).resolve().parent.parent / "tesseract" / "tessdata")
    return [d for d in out if d.is_dir()]


def find_tesseract(configured: str | None = None) -> tuple[str, str] | None:
    for source, path in _tesseract_candidates(configured):
        if path.is_file():
            return str(path), source
    return None


def _short_path(path: Path) -> str:
    """Tesseract/Leptonica open files with the ANSI API on Windows, so a data dir under a
    Vietnamese user name would fail. The 8.3 short name is plain ASCII."""
    if sys.platform != "win32":
        return str(path)
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)  # type: ignore[attr-defined]
    return buf.value if 0 < n < 1024 else str(path)


def _run(args: list[str], stdin: bytes | None = None, timeout: float = 300
         ) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, input=stdin, capture_output=True, timeout=timeout,
                          creationflags=CREATE_NO_WINDOW, check=False,
                          env={**os.environ, "OMP_THREAD_LIMIT": "2"})


def parse_tesseract_tsv(tsv: str) -> list[OCRLine]:
    """Group the word rows of ``tesseract … tsv`` output into lines."""
    lines: dict[tuple[int, int, int], dict[str, Any]] = {}
    order: list[tuple[int, int, int]] = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)
    for row in reader:
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        try:
            key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
            left, top = float(row["left"]), float(row["top"])
            right, bottom = left + float(row["width"]), top + float(row["height"])
            conf = float(row["conf"])
        except (KeyError, TypeError, ValueError):
            continue
        line = lines.get(key)
        if line is None:
            line = lines[key] = {"words": [], "bbox": [left, top, right, bottom], "conf": []}
            order.append(key)
        line["words"].append(text)
        b = line["bbox"]
        line["bbox"] = [min(b[0], left), min(b[1], top), max(b[2], right), max(b[3], bottom)]
        if conf >= 0:
            line["conf"].append(conf)
    out = []
    for key in order:
        line = lines[key]
        confs = line["conf"]
        text = " ".join(line["words"])
        mean = sum(confs) / len(confs) if confs else 0
        if len(text) <= 3 and (mean < 60 or not any(c.isalnum() for c in text)):
            continue  # specks and scanner noise
        out.append(OCRLine(text=text, bbox=tuple(line["bbox"]),
                           confidence=round(mean / 100, 3) if confs else None,
                           block=key[0] * 1000 + key[1]))
    return out


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


class TesseractOCR(OCRProvider):
    id: ClassVar[str] = "tesseract"
    name: ClassVar[str] = "Tesseract OCR (trên máy)"
    description: ClassVar[str] = (
        "Chạy trên máy, miễn phí, không gửi tài liệu ra ngoài. Bản cài Windows kèm sẵn "
        "Tesseract và dữ liệu tiếng Việt; bộ dữ liệu khác (best) được tải về thư mục models/ "
        "ở lần dùng đầu tiên.")
    docs_url: ClassVar[str | None] = "https://github.com/tesseract-ocr/tesseract"
    requires_network: ClassVar[bool] = False

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "variant": {"type": "string", "title": "Bộ dữ liệu ngôn ngữ",
                            "enum": ["fast", "best"],
                            "x-enumLabels": ["Nhanh, nhẹ (fast, ~0,5 MB tiếng Việt)",
                                             "Mô hình lớn (best, ~12 MB tiếng Việt)"],
                            "default": "fast"},
                "extra_langs": {"type": "string", "title": "Ngôn ngữ thêm",
                                "description": "Mã Tesseract, ví dụ 'eng'. Để trống nếu tài "
                                               "liệu chỉ có tiếng Việt.",
                                "default": ""},
                "psm": {"type": "integer", "title": "Chế độ phân tích trang (PSM)",
                        "enum": [3, 4, 6], "x-enumLabels": ["Tự động (3)", "Một cột (4)",
                                                            "Một khối chữ (6)"],
                        "default": 3},
                "tesseract_path": {"type": "string", "title": "Đường dẫn tesseract",
                                   "description": "Để trống để dùng bản đi kèm ứng dụng "
                                                  "hoặc bản đã cài.", "default": ""},
                "timeout_s": timeout_field(300),
            },
        }

    def _tessdata_dir(self, ctx: ProviderContext) -> Path:
        base = ctx.models_dir or Path.home() / ".videostudio-models"
        return base / "tessdata" / str(ctx.get("variant", "fast"))

    def languages(self, ctx: ProviderContext, lang: str) -> list[str]:
        langs = [TESS_LANGS.get(lang.split("-")[0].lower(), "vie")]
        for extra in str(ctx.get("extra_langs", "") or "").replace(",", "+").split("+"):
            extra = extra.strip()
            if extra and extra.isalnum() and extra not in langs:
                langs.append(extra)
        return langs

    async def ensure_languages(self, ctx: ProviderContext, langs: list[str]) -> list[str]:
        """Download missing ``<lang>.traineddata`` files; return the ones downloaded."""
        directory = self._tessdata_dir(ctx)
        directory.mkdir(parents=True, exist_ok=True)
        fetched = []
        for lang in langs:
            dest = directory / f"{lang}.traineddata"
            if dest.is_file() and dest.stat().st_size >= MIN_TRAINEDDATA_BYTES:
                continue
            if ctx.get("variant", "fast") == "fast":
                bundled = next((d / f"{lang}.traineddata" for d in bundled_tessdata_dirs()
                                if (d / f"{lang}.traineddata").is_file()), None)
                if bundled is not None:
                    shutil.copyfile(bundled, dest)
                    continue
            url = TESSDATA_URL.format(variant=ctx.get("variant", "fast"), lang=lang)
            tmp = dest.with_suffix(".part")
            try:
                async with client(DOWNLOAD_TIMEOUT_S) as http, \
                        http.stream("GET", url) as r:
                    if r.status_code >= 400:
                        raise ProviderUnavailableError(
                            f"Tesseract: không tải được dữ liệu ngôn ngữ '{lang}' "
                            f"(HTTP {r.status_code})", provider=self.name)
                    with tmp.open("wb") as fh:
                        async for chunk in r.aiter_bytes(1 << 16):
                            fh.write(chunk)
            except httpx.HTTPError as exc:
                tmp.unlink(missing_ok=True)
                raise ProviderUnavailableError(
                    f"Tesseract: không tải được dữ liệu ngôn ngữ '{lang}' "
                    f"({type(exc).__name__}). Kiểm tra kết nối mạng.", provider=self.name
                ) from exc
            if tmp.stat().st_size < MIN_TRAINEDDATA_BYTES:
                tmp.unlink(missing_ok=True)
                raise ProviderUnavailableError(
                    f"Tesseract: dữ liệu ngôn ngữ '{lang}' tải về không hợp lệ",
                    provider=self.name)
            tmp.replace(dest)
            fetched.append(lang)
        return fetched

    def _binary(self, ctx: ProviderContext) -> str:
        found = find_tesseract(ctx.get("tesseract_path"))
        if not found:
            raise ProviderConfigError(
                "Không tìm thấy Tesseract. Bản cài Windows đã kèm sẵn; khi chạy từ mã nguồn "
                "hãy cài Tesseract (apt install tesseract-ocr / UB-Mannheim trên Windows) "
                "hoặc điền đường dẫn trong cấu hình.", provider=self.name)
        return found[0]

    async def test_connection(self, ctx: ProviderContext) -> TestResult:
        try:
            binary = self._binary(ctx)
            proc = await asyncio.to_thread(_run, [binary, "--version"], None, 30)
            version = (proc.stdout or proc.stderr).decode("utf-8", "replace").splitlines()
            fetched = await self.ensure_languages(ctx, self.languages(ctx, "vi"))
        except ProviderError as exc:
            return TestResult(ok=False, message=exc.message)
        msg = f"{version[0] if version else 'tesseract'} sẵn sàng"
        if fetched:
            msg += f"; đã tải dữ liệu: {', '.join(fetched)}"
        return TestResult(ok=True, message=msg,
                          details={"binary": binary, "tessdata": str(self._tessdata_dir(ctx))})

    async def recognize(self, ctx: ProviderContext, image_path: Path, *, lang: str
                        ) -> OCRResult:
        binary = self._binary(ctx)
        langs = self.languages(ctx, lang)
        await self.ensure_languages(ctx, langs)
        tessdata = _short_path(self._tessdata_dir(ctx))
        # The image goes through stdin so Unicode paths never reach Leptonica.
        args = [binary, "stdin", "stdout", "-l", "+".join(langs), "--tessdata-dir", tessdata,
                "--psm", str(int(ctx.get("psm", 3))), "-c", "tessedit_create_tsv=1",
                "-c", "tessedit_create_txt=0"]
        try:
            proc = await asyncio.to_thread(_run, args, image_path.read_bytes(), ctx.timeout)
        except subprocess.TimeoutExpired as exc:
            raise ProviderUnavailableError("Tesseract: quá thời gian nhận dạng một trang",
                                           provider=self.name) from exc
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise ProviderUnavailableError(
                f"Tesseract lỗi (mã {proc.returncode}): {(err or ['?'])[-1][:300]}",
                provider=self.name)
        width, height = _image_size(image_path)
        lines = parse_tesseract_tsv(proc.stdout.decode("utf-8", "replace"))
        return OCRResult(lines=lines, width=width, height=height, engine="tesseract")


def _poly_bbox(points: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    xs = [float(p.get("x", 0)) for p in points] or [0.0]
    ys = [float(p.get("y", 0)) for p in points] or [0.0]
    return min(xs), min(ys), max(xs), max(ys)


def parse_google_vision(data: dict[str, Any]) -> list[OCRLine]:
    """Rebuild lines from ``fullTextAnnotation`` (symbols carry the line breaks)."""
    out: list[OCRLine] = []
    annotation = ((data.get("responses") or [{}])[0] or {}).get("fullTextAnnotation") or {}
    block_no = 0
    for page in annotation.get("pages", []):
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                block_no += 1
                text, boxes, confs = "", [], []
                for word in paragraph.get("words", []):
                    boxes.append(_poly_bbox(word.get("boundingBox", {}).get("vertices", [])))
                    if word.get("confidence") is not None:
                        confs.append(float(word["confidence"]))
                    for symbol in word.get("symbols", []):
                        text += symbol.get("text", "")
                        brk = ((symbol.get("property") or {}).get("detectedBreak") or {}).get(
                            "type")
                        if brk in ("SPACE", "SURE_SPACE"):
                            text += " "
                        elif brk in ("EOL_SURE_SPACE", "LINE_BREAK", "HYPHEN"):
                            if text.strip():
                                bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                                        max(b[2] for b in boxes), max(b[3] for b in boxes))
                                out.append(OCRLine(
                                    text=text.strip(), bbox=bbox, block=block_no,
                                    confidence=round(sum(confs) / len(confs), 3)
                                    if confs else None))
                            text, boxes, confs = "", [], []
                if text.strip() and boxes:
                    bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes))
                    out.append(OCRLine(text=text.strip(), bbox=bbox, block=block_no))
    return out


class GoogleVisionOCR(OCRProvider):
    id: ClassVar[str] = "google_vision"
    name: ClassVar[str] = "Google Cloud Vision"
    description: ClassVar[str] = ("Nhận dạng chữ trên đám mây (DOCUMENT_TEXT_DETECTION). "
                                  "Có phí sau 1.000 trang/tháng.")
    docs_url: ClassVar[str | None] = "https://cloud.google.com/vision/docs/ocr"
    base_url = "https://vision.googleapis.com/v1/images:annotate"

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "required": ["api_key"], "properties": {
            "api_key": secret_field("API key", "Google Cloud API key đã bật Cloud Vision API"),
            "timeout_s": timeout_field(60)}}

    async def _annotate(self, ctx: ProviderContext, content: bytes, lang: str
                        ) -> dict[str, Any]:
        body = {"requests": [{"image": {"content": base64.b64encode(content).decode()},
                              "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                              "imageContext": {"languageHints": [lang.split("-")[0]]}}]}
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", self.base_url, provider=self.name,
                           params={"key": ctx.get("api_key")}, json=body)
        data = r.json()
        err = ((data.get("responses") or [{}])[0] or {}).get("error")
        if err:
            raise ProviderUnavailableError(f"{self.name}: {err.get('message', err)}",
                                           provider=self.name)
        return data

    async def test_connection(self, ctx: ProviderContext) -> TestResult:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(buf, "PNG")
        try:
            await self._annotate(ctx, buf.getvalue(), "vi")
        except ProviderError as exc:
            return TestResult(ok=False, message=exc.message)
        return TestResult(ok=True, message="Kết nối Cloud Vision thành công")

    async def recognize(self, ctx, image_path, *, lang):
        data = await self._annotate(ctx, image_path.read_bytes(), lang)
        width, height = _image_size(image_path)
        return OCRResult(lines=parse_google_vision(data), width=width, height=height,
                         engine=self.id)


class AzureVisionOCR(OCRProvider):
    id: ClassVar[str] = "azure_vision"
    name: ClassVar[str] = "Azure AI Vision (Read)"
    description: ClassVar[str] = "Nhận dạng chữ trên đám mây của Microsoft (Image Analysis 4.0)."
    docs_url: ClassVar[str | None] = (
        "https://learn.microsoft.com/azure/ai-services/computer-vision/overview-ocr")

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "required": ["endpoint", "api_key"], "properties": {
            "endpoint": {"type": "string", "title": "Endpoint",
                         "description": "Ví dụ https://<tên>.cognitiveservices.azure.com"},
            "api_key": secret_field("Key"),
            "timeout_s": timeout_field(60)}}

    async def _analyze(self, ctx: ProviderContext, content: bytes) -> dict[str, Any]:
        endpoint = str(ctx.get("endpoint", "")).rstrip("/")
        if not endpoint.startswith("https://"):
            raise ProviderConfigError(f"{self.name}: endpoint phải bắt đầu bằng https://",
                                      provider=self.name)
        async with client(ctx.timeout) as http:
            r = await send(http, "POST",
                           f"{endpoint}/computervision/imageanalysis:analyze",
                           provider=self.name,
                           params={"api-version": "2024-02-01", "features": "read"},
                           headers={"Ocp-Apim-Subscription-Key": ctx.get("api_key"),
                                    "Content-Type": "application/octet-stream"},
                           content=content)
        return r.json()

    async def test_connection(self, ctx):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(buf, "PNG")
        try:
            await self._analyze(ctx, buf.getvalue())
        except ProviderError as exc:
            return TestResult(ok=False, message=exc.message)
        return TestResult(ok=True, message="Kết nối Azure AI Vision thành công")

    async def recognize(self, ctx, image_path, *, lang):
        data = await self._analyze(ctx, image_path.read_bytes())
        lines = []
        for n, block in enumerate((data.get("readResult") or {}).get("blocks", []), 1):
            for line in block.get("lines", []):
                words = line.get("words") or []
                confs = [float(w["confidence"]) for w in words if "confidence" in w]
                lines.append(OCRLine(text=line.get("text", ""),
                                     bbox=_poly_bbox(line.get("boundingPolygon", [])),
                                     confidence=round(sum(confs) / len(confs), 3)
                                     if confs else None, block=n))
        width, height = _image_size(image_path)
        return OCRResult(lines=lines, width=width, height=height, engine=self.id)


class MockOCR(OCRProvider):
    """Returns a fixed Vietnamese text: lets the scanned-PDF path run without an engine."""

    id: ClassVar[str] = "mock_ocr"
    name: ClassVar[str] = "OCR giả lập (offline)"
    description: ClassVar[str] = "Trả về một đoạn chữ mẫu cho mỗi trang. Chỉ dùng để thử."
    requires_network: ClassVar[bool] = False
    is_mock: ClassVar[bool] = True

    default_text = ("Trang được nhận dạng bằng OCR giả lập.\n"
                    "Nội dung tiếng Việt có dấu: Hà Nội, Đà Nẵng, Huế.")

    def config_schema(self):
        return {"type": "object", "properties": {
            "text": {"type": "string", "title": "Chữ trả về", "format": "textarea",
                     "default": self.default_text}}}

    async def test_connection(self, ctx):
        return TestResult(ok=True, message="OCR giả lập luôn sẵn sàng")

    async def recognize(self, ctx, image_path, *, lang):
        width, height = _image_size(image_path)
        rows = [r for r in str(ctx.get("text") or self.default_text).splitlines() if r.strip()]
        lines = [OCRLine(text=r.strip(), bbox=(50, 60 + 40 * i, width - 50, 90 + 40 * i),
                         confidence=1.0, block=1) for i, r in enumerate(rows)]
        return OCRResult(lines=lines, width=width, height=height, engine=self.id)


PROVIDERS = [TesseractOCR, GoogleVisionOCR, AzureVisionOCR, MockOCR]
