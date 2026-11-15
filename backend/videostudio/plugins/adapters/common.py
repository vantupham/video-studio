from __future__ import annotations

from pathlib import Path
from typing import Any

from videostudio.plugins.base import SourceRef
from videostudio.textutil import normalize_text, read_text_file

LANGUAGE_FIELD: dict[str, Any] = {
    "type": "string",
    "title": "Ngôn ngữ",
    "enum": ["vi", "en"],
    "x-enumLabels": ["Tiếng Việt", "English"],
    "default": "vi",
}

TITLE_FIELD: dict[str, Any] = {
    "type": "string",
    "title": "Tiêu đề",
    "description": "Để trống để tự lấy từ nội dung hoặc tên file.",
    "default": "",
}


def read_source_text(source: SourceRef) -> str:
    if source.path is not None:
        return read_text_file(source.path)
    if source.type == "text":
        return normalize_text(source.uri.removeprefix("inline:"))
    raise ValueError(f"Source {source.uri!r} has no readable content")


def apply_defaults(schema: dict[str, Any], config: dict[str, Any] | None) -> dict[str, Any]:
    merged = {
        key: prop["default"]
        for key, prop in schema.get("properties", {}).items()
        if "default" in prop
    }
    merged.update({k: v for k, v in (config or {}).items() if v is not None})
    return merged


def file_stem_title(source: SourceRef) -> str | None:
    if source.type == "file" and source.filename:
        return Path(source.filename).stem.replace("_", " ").strip() or None
    return None
