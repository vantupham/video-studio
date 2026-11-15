"""Text helpers that keep Vietnamese text intact."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


def decode_bytes(data: bytes) -> str:
    """Decode text files: UTF-8 (with/without BOM), UTF-16 with BOM, then cp1258.

    cp1258 is the legacy Windows code page for Vietnamese; it is only tried when
    the data is not valid UTF-8.
    """
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return data.decode(encoding)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1258")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def normalize_text(text: str) -> str:
    """NFC-normalize (macOS/Word often produce NFD for Vietnamese) and unify newlines."""
    text = unicodedata.normalize("NFC", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_file(path: Path) -> str:
    return normalize_text(decode_bytes(path.read_bytes()))


_WS = re.compile(r"[ \t\u00a0]+")


def collapse_spaces(text: str) -> str:
    return _WS.sub(" ", text).strip()


def word_count(text: str) -> int:
    return len(text.split())


# Average Vietnamese narration speed is roughly 140-160 words per minute.
WORDS_PER_SECOND = 2.5


def estimate_duration_s(words: int) -> float:
    return round(words / WORDS_PER_SECOND, 1)


_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, default: str = "file") -> str:
    """Make a filename safe on Windows while keeping Unicode (diacritics) intact."""
    name = unicodedata.normalize("NFC", name).strip()
    name = _UNSAFE_FILENAME.sub("_", name).strip(" .")
    stem = Path(name).stem.upper()
    if not name or stem in _RESERVED_WINDOWS_NAMES:
        name = f"_{name}" if name else default
    return name[:150]


_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
