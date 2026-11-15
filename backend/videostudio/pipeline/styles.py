"""Style packs: ``style.yaml`` + Jinja prompt templates + assets.

Built-in styles live in ``videostudio/styles_builtin/<id>/``. Users can:
* add a whole new style in ``<data dir>/styles/<id>/style.yaml``;
* override single prompt templates of any style in ``<data dir>/styles/<id>/prompts/*.j2``
  (a user ``style.yaml`` for a built-in id is merged over the built-in one).
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import jinja2
import yaml

PACKAGE_DIR = Path(__file__).resolve().parent.parent


def _resource_dir(name: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = base / "videostudio" / name
        if candidate.is_dir():
            return candidate
    return PACKAGE_DIR / name


BUILTIN_STYLES_DIR = _resource_dir("styles_builtin")
FONTS_DIR = _resource_dir("assets") / "fonts"
LANGUAGE_NAMES = {"vi": "tiếng Việt", "en": "tiếng Anh (English)"}
PROMPTS = ("system", "script", "scene")


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


DEFAULTS: dict[str, Any] = {
    "aspect": "16:9",
    "fps": 30,
    "pacing": {"words_per_second": 2.6, "scene_padding_s": 0.4, "min_scene_s": 2.0,
               "fade_s": 0.25},
    "colors": {"background": "#0f172a", "background2": "#1e293b", "text": "#ffffff",
               "accent": "#38bdf8", "muted": "#cbd5e1"},
    "fonts": {"title": "BeVietnamPro-Bold.ttf", "body": "BeVietnamPro-Medium.ttf"},
    "cards": {"title_size": 0.08, "heading_size": 0.06, "body_size": 0.04, "margin": 0.08},
    "captions": {"mode": "line", "font": "Be Vietnam Pro SemiBold", "size": 0.046,
                 "bold": False, "uppercase": False, "position": "bottom", "margin_v": 0.05,
                 "max_chars": 42, "max_lines": 2, "max_words": 12, "primary": "#FFFFFF",
                 "highlight": "#FACC15", "outline": "#000000", "outline_width": 0.003,
                 "shadow": 0.0015, "box": False},
    "script": {"scene_seconds": 8},
    # type: none | fade (through black, per scene) | crossfade | any ffmpeg xfade name
    # (slideleft, wipeleft, dissolve, circleopen, …)
    "transitions": {"type": "fade", "duration_s": 0.5},
    # Slow pan/zoom on image scenes. mode: alternate | zoom_in | zoom_out | pan_left |
    # pan_right
    "ken_burns": {"enabled": True, "zoom": 1.12, "mode": "alternate"},
}

RESOLUTIONS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


@dataclass
class StylePack:
    id: str
    data: dict[str, Any]
    dirs: list[Path]  # highest priority first (user dir, built-in dir)

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.id))

    @property
    def aspect(self) -> str:
        return str(self.data["aspect"])

    @property
    def fps(self) -> int:
        return int(self.data["fps"])

    def section(self, key: str) -> dict[str, Any]:
        return self.data.get(key, {})

    def resolution(self, aspect: str | None = None, preview: bool = False) -> tuple[int, int]:
        w, h = RESOLUTIONS.get(aspect or self.aspect, RESOLUTIONS["16:9"])
        if preview:
            return w // 2, h // 2
        return w, h

    def font_path(self, role: str) -> Path:
        name = self.section("fonts").get(role) or DEFAULTS["fonts"].get(role, "")
        for d in self.dirs:
            candidate = d / "assets" / name
            if candidate.is_file():
                return candidate
        return FONTS_DIR / name

    @cached_property
    def jinja(self) -> jinja2.Environment:
        loaders = [jinja2.FileSystemLoader(str(d / "prompts")) for d in self.dirs
                   if (d / "prompts").is_dir()]
        return jinja2.Environment(loader=jinja2.ChoiceLoader(loaders),
                                  undefined=jinja2.StrictUndefined, autoescape=False,
                                  keep_trailing_newline=False)

    def prompt_source(self, name: str) -> tuple[str, str]:
        """Return ``(text, origin)`` of a prompt template (origin: user|builtin)."""
        for d in self.dirs:
            path = d / "prompts" / f"{name}.j2"
            if path.is_file():
                origin = "builtin" if BUILTIN_STYLES_DIR in path.parents else "user"
                return path.read_text(encoding="utf-8"), origin
        raise FileNotFoundError(f"prompt {name}.j2 not found for style {self.id}")

    def render_prompt(self, name: str, **variables: Any) -> str:
        variables.setdefault("style", self.data)
        lang = variables.get("language", "vi")
        variables.setdefault("language_name", LANGUAGE_NAMES.get(lang, lang))
        if "payload" in variables:
            variables["payload_json"] = json.dumps(variables.pop("payload"), ensure_ascii=False,
                                                   indent=2)
        return self.jinja.get_template(f"{name}.j2").render(**variables).strip()

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.data.get("description", ""),
            "aspect": self.aspect,
            "captions_mode": self.section("captions").get("mode"),
            "customized": len(self.dirs) > 1 or BUILTIN_STYLES_DIR not in self.dirs[0].parents,
        }


class StyleLibrary:
    def __init__(self, user_dir: Path | None = None, builtin_dir: Path = BUILTIN_STYLES_DIR):
        self.user_dir = user_dir
        self.builtin_dir = builtin_dir

    def _style_dirs(self) -> dict[str, list[Path]]:
        found: dict[str, list[Path]] = {}
        for root in (self.builtin_dir, self.user_dir):
            if root is None or not root.is_dir():
                continue
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and not entry.name.startswith(("_", ".")):
                    if (entry / "style.yaml").is_file() or entry.name in found:
                        found.setdefault(entry.name, []).insert(0, entry)
        return found

    def ids(self) -> list[str]:
        return list(self._style_dirs())

    def get(self, style_id: str) -> StylePack:
        dirs = self._style_dirs().get(style_id)
        if not dirs:
            raise KeyError(f"Không tìm thấy style: {style_id}")
        data = copy.deepcopy(DEFAULTS)
        for d in reversed(dirs):  # built-in first, user overrides last
            path = d / "style.yaml"
            if path.is_file():
                loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                data = _merge(data, loaded)
        data["id"] = style_id
        return StylePack(id=style_id, data=data, dirs=dirs)

    def list(self) -> list[dict[str, Any]]:
        out = []
        for sid in self.ids():
            try:
                out.append(self.get(sid).public())
            except (yaml.YAMLError, OSError) as exc:
                out.append({"id": sid, "name": sid, "error": str(exc)})
        return out
