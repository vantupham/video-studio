"""Subtitle generation (SRT, WebVTT, ASS) from word timings.

* ``line`` mode (explainer): readable cues of up to ``max_lines`` × ``max_chars``.
* ``word_highlight`` mode (shorts): short cues of ``max_words`` words; the ASS output has
  one event per word where the spoken word is highlighted (karaoke style).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from videostudio.providers.base import WordTiming

_BREAK_AFTER = (".", "!", "?", "…", ",", ";", ":")
_HARD_BREAK = (".", "!", "?", "…")


@dataclass
class Cue:
    start: float
    end: float
    words: list[WordTiming] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def offset_words(words: list[WordTiming], offset: float) -> list[WordTiming]:
    return [WordTiming(text=w.text, start=round(w.start + offset, 3),
                       end=round(w.end + offset, 3)) for w in words]


def build_cues(words: list[WordTiming], *, max_chars: int = 42, max_lines: int = 2,
               max_words: int = 14, max_duration: float = 6.0, min_duration: float = 0.5,
               max_gap: float = 1.2) -> list[Cue]:
    """Group words into cues: break on sentence ends, long pauses, length limits, and
    prefer breaking after commas once a cue is reasonably long."""
    limit_chars = max_chars * max_lines
    cues: list[Cue] = []
    current: list[WordTiming] = []

    def flush() -> None:
        if current:
            cues.append(Cue(start=current[0].start, end=current[-1].end, words=list(current)))
            current.clear()

    for word in words:
        if current:
            text_len = len(" ".join(w.text for w in current)) + 1 + len(word.text)
            gap = word.start - current[-1].end
            too_long = (text_len > limit_chars or len(current) >= max_words
                        or word.end - current[0].start > max_duration)
            if too_long or gap > max_gap:
                flush()
        current.append(word)
        if word.text.endswith(_HARD_BREAK):
            flush()
        elif word.text.endswith(_BREAK_AFTER) and len(current) >= max(2, max_words // 2):
            flush()
    flush()

    # Monotonic, non-overlapping, minimum duration where room allows.
    for i, cue in enumerate(cues):
        prev_end = cues[i - 1].end if i else 0.0
        cue.start = max(cue.start, prev_end)
        nxt = cues[i + 1].start if i + 1 < len(cues) else None
        if cue.end - cue.start < min_duration:
            cue.end = cue.start + min_duration
        if nxt is not None and cue.end > nxt:
            cue.end = max(cue.start + 0.05, nxt)
        cue.start, cue.end = round(cue.start, 3), round(cue.end, 3)
    return cues


def wrap_lines(text: str, max_chars: int, max_lines: int) -> list[str]:
    words = text.split()
    if len(text) <= max_chars or max_lines <= 1:
        return [text]
    # balanced split into max_lines lines
    target = len(text) / max_lines
    lines: list[str] = []
    line: list[str] = []
    for w in words:
        candidate = " ".join(line + [w])
        if line and len(candidate) > max(target, 1) and len(lines) < max_lines - 1:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))
    return lines


def _ts(seconds: float, sep: str) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(cues: list[Cue], max_chars: int = 42, max_lines: int = 2) -> str:
    blocks = []
    for i, cue in enumerate(cues, 1):
        body = "\n".join(wrap_lines(cue.text, max_chars, max_lines))
        blocks.append(f"{i}\n{_ts(cue.start, ',')} --> {_ts(cue.end, ',')}\n{body}\n")
    return "\n".join(blocks)


def to_vtt(cues: list[Cue], max_chars: int = 42, max_lines: int = 2) -> str:
    blocks = ["WEBVTT\n"]
    for cue in cues:
        body = "\n".join(wrap_lines(cue.text, max_chars, max_lines))
        blocks.append(f"{_ts(cue.start, '.')} --> {_ts(cue.end, '.')}\n{body}\n")
    return "\n".join(blocks)


# ----------------------------------------------------------------------------- ASS


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """``#RRGGBB`` → ``&HAABBGGRR`` (alpha 0 = opaque)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_ts(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def to_ass(cues: list[Cue], *, width: int, height: int, captions: dict[str, Any],
           title: str = "Video Studio") -> str:
    size = max(12, round(float(captions.get("size", 0.046)) * height))
    outline = max(0.0, float(captions.get("outline_width", 0.003)) * height)
    shadow = max(0.0, float(captions.get("shadow", 0.0)) * height)
    position = captions.get("position", "bottom")
    alignment = {"bottom": 2, "center": 5, "top": 8}.get(position, 2)
    margin_v = round(float(captions.get("margin_v", 0.05)) * height)
    if position == "center":
        alignment = 2  # bottom-anchored at margin_v → lower-middle for shorts
    margin_h = round(width * 0.06)
    primary = ass_color(captions.get("primary", "#FFFFFF"))
    highlight = ass_color(captions.get("highlight", "#FACC15"))
    outline_color = ass_color(captions.get("outline", "#000000"))
    box = bool(captions.get("box"))
    border_style = 3 if box else 1
    if box:
        outline_color = ass_color("#000000", 0x60)
        outline = max(outline, size * 0.18)
    back = ass_color("#000000", 0x80)
    bold = -1 if captions.get("bold") else 0
    uppercase = bool(captions.get("uppercase"))
    max_chars = int(captions.get("max_chars", 42))
    max_lines = int(captions.get("max_lines", 2))
    font = captions.get("font", "Be Vietnam Pro SemiBold")
    word_mode = captions.get("mode") == "word_highlight"

    header = (
        "[Script Info]\n"
        f"Title: {ass_escape(title)}\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "YCbCr Matrix: TV.709\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},{primary},{highlight},{outline_color},{back},{bold},0,0,0,"
        f"100,100,0,0,{border_style},{outline:.1f},{shadow:.1f},{alignment},{margin_h},"
        f"{margin_h},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    def fmt(text: str) -> str:
        return text.upper() if uppercase else text

    events: list[str] = []
    for cue in cues:
        if not word_mode:
            lines = wrap_lines(fmt(cue.text), max_chars, max_lines)
            body = r"\N".join(ass_escape(line) for line in lines)
            events.append(f"Dialogue: 0,{_ass_ts(cue.start)},{_ass_ts(cue.end)},Default,,0,0,0,,"
                          f"{body}")
            continue
        words = cue.words
        for i, word in enumerate(words):
            start = cue.start if i == 0 else max(cue.start, word.start)
            end = words[i + 1].start if i + 1 < len(words) else cue.end
            if end <= start:
                continue
            parts = []
            for j, other in enumerate(words):
                token = ass_escape(fmt(other.text))
                if j == i:
                    parts.append(r"{\c" + highlight + r"&\fscx108\fscy108}" + token
                                 + r"{\c" + primary + r"&\fscx100\fscy100}")
                else:
                    parts.append(token)
            lines = wrap_lines(" ".join(fmt(w.text) for w in words), max_chars, max_lines)
            # re-insert line breaks at the same word boundaries
            body, idx = [], 0
            for line in lines:
                n = len(line.split())
                body.append(" ".join(parts[idx:idx + n]))
                idx += n
            events.append(f"Dialogue: 0,{_ass_ts(start)},{_ass_ts(end)},Default,,0,0,0,,"
                          + r"\N".join(body))
    return header + "\n".join(events) + "\n"


def build_subtitles(words: list[WordTiming], captions: dict[str, Any], *, width: int,
                    height: int, title: str) -> dict[str, str]:
    max_chars = int(captions.get("max_chars", 42))
    max_lines = int(captions.get("max_lines", 2))
    word_mode = captions.get("mode") == "word_highlight"
    display = build_cues(words, max_chars=max_chars, max_lines=max_lines,
                         max_words=int(captions.get("max_words", 14)),
                         max_duration=3.0 if word_mode else 6.0)
    # SRT/VTT are for players & upload: always readable line cues.
    readable = display if not word_mode else build_cues(words, max_chars=42, max_lines=2,
                                                        max_words=14)
    return {
        "srt": to_srt(readable, 42 if word_mode else max_chars, 2 if word_mode else max_lines),
        "vtt": to_vtt(readable, 42 if word_mode else max_chars, 2 if word_mode else max_lines),
        "ass": to_ass(display, width=width, height=height, captions=captions, title=title),
    }
