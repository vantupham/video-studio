"""Scene frame images drawn with Pillow (title cards, text cards, image layouts)."""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .models import Overlay, Visual
from .styles import StylePack


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@lru_cache(maxsize=64)
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _nfc(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def gradient(size: tuple[int, int], c1: str, c2: str) -> Image.Image:
    w, h = size
    a, b = _rgb(c1), _rgb(c2)
    base = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        base.putpixel((0, y), tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    img = base.resize((w, h), Image.BILINEAR)
    # soft diagonal light
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse([-w * 0.3, -h * 0.5, w * 0.8, h * 0.7], fill=38)
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(w, h) // 8))
    return Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), img, glow)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if font.getlength(candidate) <= max_width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
    return lines


def fit_text(text: str, font_path: Path, start_size: int, max_width: int, max_height: int,
             line_spacing: float = 1.22, min_size: int = 14) -> tuple[ImageFont.FreeTypeFont,
                                                                     list[str], int]:
    size = start_size
    while True:
        font = _font(str(font_path), size)
        lines = wrap(text, font, max_width)
        line_h = int(size * line_spacing)
        if line_h * len(lines) <= max_height or size <= min_size:
            return font, lines, line_h
        size = int(size * 0.92)


def _draw_lines(draw: ImageDraw.ImageDraw, lines: list[str], font, x: int, y: int, line_h: int,
                fill, align: str = "left", width: int = 0, shadow: bool = True) -> int:
    for line in lines:
        lx = x
        if align == "center":
            lx = x + (width - int(font.getlength(line))) // 2
        if shadow:
            draw.text((lx + 2, y + 3), line, font=font, fill=(0, 0, 0, 110))
        draw.text((lx, y), line, font=font, fill=fill)
        y += line_h
    return y


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(0.5, 0.45))


def _contain_on_blur(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    bg = _cover(img, size).filter(ImageFilter.GaussianBlur(radius=max(size) // 40))
    bg = Image.blend(bg, Image.new("RGB", size, (0, 0, 0)), 0.35)
    fg = ImageOps.contain(img, (int(size[0] * 0.9), int(size[1] * 0.82)), Image.LANCZOS)
    bg.paste(fg, ((size[0] - fg.width) // 2, (size[1] - fg.height) // 2))
    return bg


def render_card(visual: Visual, style: StylePack, size: tuple[int, int],
                overlays: list[Overlay] | None = None, image_path: Path | None = None,
                caption_safe: bool = True) -> Image.Image:
    w, h = size
    colors = style.section("colors")
    cards = style.section("cards")
    margin = int(min(w, h) * float(cards.get("margin", 0.08)))
    text_color = _rgb(colors.get("text", "#ffffff"))
    accent = _rgb(colors.get("accent", "#38bdf8"))
    muted = _rgb(colors.get("muted", "#cbd5e1"))
    title_font = style.font_path("title")
    body_font = style.font_path("body")
    portrait = h > w
    # keep the caption area free (bottom for 16:9, lower-middle for 9:16)
    caption_band = int(h * (0.36 if portrait else 0.16)) if caption_safe else margin
    bg_color = visual.color or colors.get("background", "#0f172a")

    if visual.type in ("image", "stock") and image_path and image_path.is_file():
        with Image.open(image_path) as src:
            src = ImageOps.exif_transpose(src).convert("RGB")
            img = _cover(src, size) if visual.type == "stock" else _contain_on_blur(src, size)
    elif visual.type == "solid":
        img = Image.new("RGB", size, _rgb(bg_color))
    else:
        img = gradient(size, bg_color, colors.get("background2", "#1e293b"))
    img = img.convert("RGBA")
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    title = _nfc(visual.title)
    usable_w = w - 2 * margin

    if visual.type == "title_card" or (visual.type == "solid" and title):
        size_px = int(h * float(cards.get("title_size", 0.08)) * (0.75 if portrait else 1))
        font, lines, line_h = fit_text(title or " ", title_font, size_px, usable_w,
                                       int(h * 0.4))
        subtitle = _nfc(visual.subtitle)
        sub_font = _font(str(body_font), int(size_px * 0.45))
        sub_lines = wrap(subtitle, sub_font, usable_w) if subtitle else []
        block_h = line_h * len(lines) + (int(size_px * 0.6) * len(sub_lines) + size_px // 2
                                         if sub_lines else 0)
        top = (h - caption_band // 2 - block_h) // 2 if portrait else (h - block_h) // 2
        y = _draw_lines(draw, lines, font, margin, top, line_h, text_color, "center", usable_w)
        bar_w = int(w * 0.12)
        draw.rounded_rectangle([(w - bar_w) // 2, y + size_px // 5, (w + bar_w) // 2,
                                y + size_px // 5 + max(4, h // 120)], radius=4, fill=accent)
        if sub_lines:
            _draw_lines(draw, sub_lines, sub_font, margin, y + size_px // 2, int(size_px * 0.6),
                        muted, "center", usable_w)
    elif visual.type == "text_card":
        head_px = int(h * float(cards.get("heading_size", 0.06)) * (0.85 if portrait else 1))
        body_px = int(h * float(cards.get("body_size", 0.042)) * (0.9 if portrait else 1))
        top = int(h * (0.12 if portrait else 0.1))
        y = top
        if title:
            font, lines, line_h = fit_text(title, title_font, head_px, usable_w, int(h * 0.22))
            y = _draw_lines(draw, lines, font, margin, y, line_h, text_color,
                            "center" if portrait else "left", usable_w)
            ux = margin if not portrait else (w - int(w * 0.14)) // 2
            draw.rounded_rectangle([ux, y + 8, ux + int(w * 0.14), y + 8 + max(4, h // 140)],
                                   radius=4, fill=accent)
            y += int(head_px * 0.8)
        bullets = [_nfc(b) for b in visual.bullets if _nfc(b)]
        avail_h = h - caption_band - y
        if bullets:
            dot = max(6, body_px // 4)
            indent = dot * 3
            font = _font(str(body_font), body_px)
            # shrink until all bullets fit
            while True:
                wrapped = [wrap(b, font, usable_w - indent) for b in bullets]
                line_h = int(font.size * 1.3)
                total = sum(len(x) for x in wrapped) * line_h + (len(bullets) - 1) * line_h // 2
                if total <= avail_h or font.size <= 16:
                    break
                font = _font(str(body_font), int(font.size * 0.9))
            y += max(0, (avail_h - total) // 3) if portrait else 0
            for lines in wrapped:
                cy = y + line_h // 2
                draw.ellipse([margin, cy - dot // 2, margin + dot, cy + dot // 2], fill=accent)
                y = _draw_lines(draw, lines, font, margin + indent, y, line_h, text_color)
                y += line_h // 2
    elif title and visual.type in ("image", "stock"):
        font, lines, line_h = fit_text(title, title_font, int(h * 0.05), usable_w, int(h * 0.16))
        band_h = line_h * len(lines) + margin // 2
        draw.rectangle([0, 0, w, band_h + margin // 2], fill=(0, 0, 0, 120))
        _draw_lines(draw, lines, font, margin, margin // 2, line_h, text_color)

    for overlay in overlays or []:
        text = _nfc(overlay.text)
        font = _font(str(body_font), int(h * 0.04))
        lines = wrap(text, font, usable_w)
        line_h = int(font.size * 1.25)
        block_h = line_h * len(lines)
        y = {"top": margin, "center": (h - block_h) // 2,
             "bottom": h - caption_band - block_h - margin // 2}[overlay.position]
        pad = font.size // 2
        if overlay.type == "lower_third":
            draw.rectangle([0, y - pad, int(w * 0.7), y + block_h + pad], fill=(*accent, 220))
            _draw_lines(draw, lines, font, margin, y, line_h, (15, 23, 42), shadow=False)
        else:
            widest = max(int(font.getlength(line)) for line in lines)
            x0 = (w - widest) // 2
            draw.rounded_rectangle([x0 - pad, y - pad, x0 + widest + pad, y + block_h + pad],
                                   radius=pad, fill=(0, 0, 0, 150))
            _draw_lines(draw, lines, font, margin, y, line_h, text_color, "center", usable_w,
                        shadow=False)

    return Image.alpha_composite(img, layer).convert("RGB")
