"""Locate the canonical Content IR JSON Schema (``packages/ir-schema``)."""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_FILENAME = "content-ir.schema.json"


def schema_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = [here / SCHEMA_FILENAME]
    # PyInstaller onedir bundles data under sys._MEIPASS (the "_internal" folder).
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "videostudio" / "ir" / SCHEMA_FILENAME)
    # Source checkout: backend/videostudio/ir -> repo root.
    candidates.append(here.parents[2] / "packages" / "ir-schema" / SCHEMA_FILENAME)
    return candidates


@lru_cache(maxsize=1)
def load_json_schema() -> dict[str, Any] | None:
    for path in schema_candidates():
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None
