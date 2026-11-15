"""Automatic thumbnail: a frame from the video with the title in the style's bundled font.
Output is a JPEG under 2 MB (YouTube's limit), 1280×720 or 720×1280."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from videostudio.media.ffmpeg import require_ffmpeg
from videostudio.pipeline.cards import _nfc, _rgb, fit_text
from videostudio.pipeline.styles import StylePack

MAX_BYTES = 2 * 1024 * 1024
SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1080, 1080)}


def extract_frame(video: Path, at_s: float) -> Image.Image:
    ffmpeg = require_ffmpeg().ffmpeg
    assert ffmpeg
    proc = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{max(0.0, at_s):.2f}", "-i",
                           str(video), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png",
                           "-"], capture_output=True, timeout=60, check=False,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"Không lấy được khung hình: {proc.stderr.decode()[-200:]}")
    return Image.open(io.BytesIO(proc.stdout)).convert("RGB")


def compose(frame: Image.Image, title: str, style: StylePack, aspect: str,
            show_title: bool = True) -> Image.Image:
    w, h = SIZES.get(aspect, SIZES["16:9"])
    img = ImageOps.fit(frame, (w, h), Image.LANCZOS)
    title = _nfc(title)
    if not show_title or not title:
        return img
    colors = style.section("colors")
    accent = _rgb(colors.get("accent", "#38bdf8"))
    # darken the lower part so the title is readable on any frame
    shade = Image.new("L", (w, h), 0)
    band_top = int(h * (0.45 if aspect != "9:16" else 0.55))
    draw = ImageDraw.Draw(shade)
    for y in range(band_top, h):
        draw.line([(0, y), (w, y)], fill=int(215 * (y - band_top) / max(1, h - band_top)))
    img = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), img, shade)
    margin = int(w * 0.06)
    max_h = int(h * (0.36 if aspect != "9:16" else 0.28))
    font, lines, line_h = fit_text(title, style.font_path("title"), int(h * 0.13),
                                   w - 2 * margin, max_h, line_spacing=1.12,
                                   min_size=int(h * 0.04))
    block_h = line_h * len(lines)
    y = h - margin - block_h
    canvas = img.convert("RGBA")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rectangle([margin, y - int(h * 0.035), margin + int(w * 0.14), y - int(h * 0.02)],
                fill=(*accent, 255))
    stroke = max(2, font.size // 14)
    for line in lines:
        d.text((margin, y), line, font=font, fill=(255, 255, 255, 255), stroke_width=stroke,
               stroke_fill=(0, 0, 0, 200))
        y += line_h
    shadow = layer.filter(ImageFilter.GaussianBlur(radius=max(2, font.size // 10)))
    shadow_alpha = shadow.getchannel("A").point(lambda a: int(a * 0.6))
    canvas.paste((0, 0, 0, 255), (0, 0), shadow_alpha)
    canvas = Image.alpha_composite(canvas, layer)
    return canvas.convert("RGB")


def save_jpeg(img: Image.Image, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for quality in (92, 85, 78, 70, 60):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        if buf.tell() <= MAX_BYTES:
            break
    dest.write_bytes(buf.getvalue())
    return dest


def make_thumbnail(video: Path, dest: Path, title: str, style: StylePack, aspect: str,
                   at_s: float, show_title: bool = True) -> Path:
    return save_jpeg(compose(extract_frame(video, at_s), title, style, aspect, show_title),
                     dest)


def prepare_custom(source: Path, dest: Path, aspect: str) -> Path:
    with Image.open(source) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")
    w, h = SIZES.get(aspect, SIZES["16:9"])
    return save_jpeg(ImageOps.fit(img, (w, h), Image.LANCZOS), dest)
