"""Content IR → Storyboard, through an LLM (or the "use text as-is" splitter)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from videostudio.ir import ContentIR
from videostudio.providers.base import ChatMessage, ResolvedProvider
from videostudio.providers.llm_json import complete_json
from videostudio.textutil import normalize_text

from .models import DraftScene, GenerationSettings, Scene, ScriptDraft, Storyboard, Visual
from .styles import StylePack

_SENTENCES = re.compile(r"(?<=[.!?…])\s+")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


def _list_items(items: list[Any], depth: int = 0) -> list[str]:
    out = []
    for item in items:
        out.append(("  " * depth) + item.text)
        out.extend(_list_items(item.items, depth + 1))
    return out


def _as_sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?…:;" else text + "."


def ir_sections(ir: ContentIR, image_resolver=None) -> list[dict[str, Any]]:
    """Flatten the IR into ``{id, heading, text, bullets, images, block_ids}`` sections."""
    sections = []
    for section in ir.sections:
        paragraphs: list[str] = []
        bullets: list[str] = []
        images: list[str] = []
        block_ids: list[str] = []
        heading = section.title
        for block in section.blocks:
            if block.id:
                block_ids.append(block.id)
            kind = block.kind
            if kind == "heading" and not heading:
                heading = block.text
            elif kind == "heading":
                if block.text.strip() != (heading or "").strip():
                    paragraphs.append(_as_sentence(block.text))
            elif kind == "paragraph":
                paragraphs.append(block.text)
            elif kind == "list":
                items = _list_items(block.items)
                bullets.extend(i.strip() for i in items)
                paragraphs.append(" ".join(_as_sentence(i) for i in items))
            elif kind == "table":
                rows = [", ".join(f"{h}: {c}" for h, c in zip(block.header, r, strict=False))
                        if block.header else ", ".join(r) for r in block.rows]
                paragraphs.append(". ".join(rows) + ".")
            elif kind == "image":
                resolved = image_resolver(block.src) if image_resolver else None
                if resolved:
                    images.append(resolved)
                if block.caption or block.alt:
                    paragraphs.append(block.caption or block.alt)
        text = normalize_text(" ".join(p.strip() for p in paragraphs if p.strip()))
        if text or bullets or images:
            sections.append({"id": section.id, "heading": heading or ir.title, "text": text,
                             "bullets": bullets[:8], "images": images,
                             "block_ids": block_ids})
    return sections


def make_image_resolver(data_dir: Path, source_dirs: list[Path]):
    """Map an IR image ``src`` to a path relative to the data dir if the file exists
    locally (next to the source document)."""

    def resolve(src: str) -> str | None:
        if re.match(r"^[a-z]+://", src, re.I) or src.startswith("data:"):
            return None
        for base in source_dirs:
            candidate = (base / src).resolve()
            if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTS:
                try:
                    return candidate.relative_to(data_dir.resolve()).as_posix()
                except ValueError:
                    return None
        return None

    return resolve


def script_payload(ir: ContentIR, sections: list[dict[str, Any]], style: StylePack,
                   settings: GenerationSettings, max_chars: int = 14000) -> dict[str, Any]:
    wps = float(style.section("pacing").get("words_per_second", 2.6))
    budget = max_chars
    out_sections = []
    for s in sections:
        text = s["text"][: max(200, budget)]
        budget -= len(text)
        out_sections.append({"heading": s["heading"], "text": text, "bullets": s["bullets"],
                             "images": s["images"]})
        if budget <= 0:
            break
    first = next((s["text"] for s in sections if s["text"]), "")
    return {
        "task": "script",
        "title": ir.title,
        "summary": _SENTENCES.split(first)[0][:300] if first else "",
        "language": settings.language,
        "target_duration_s": settings.target_duration_s,
        "words_per_second": wps,
        "max_scenes": settings.max_scenes,
        "style": style.id,
        "sections": out_sections,
    }


def _scene_id(i: int) -> str:
    return f"s{i + 1}"


def draft_scene_to_scene(draft: DraftScene, scene_id: str, block_ids: list[str] | None = None,
                         image: str | None = None) -> Scene:
    visual_type = draft.visual_type
    if visual_type == "image" and not image:
        visual_type = "stock" if draft.image_query else "text_card"
    visual = Visual(
        type=visual_type,
        title=(draft.title or None),
        bullets=[b for b in draft.bullets if b.strip()][:6],
        image=image if visual_type == "image" else None,
        query=draft.image_query if visual_type == "stock" else None,
    )
    return Scene(id=scene_id, narration=normalize_text(draft.narration), visual=visual,
                 source_block_ids=block_ids or [])


def draft_to_storyboard(draft: ScriptDraft, style: StylePack, settings: GenerationSettings,
                        images: list[str] | None = None) -> Storyboard:
    images = list(images or [])
    scenes = []
    for i, d in enumerate(draft.scenes[: settings.max_scenes + 2]):
        image = images.pop(0) if d.visual_type == "image" and images else None
        scenes.append(draft_scene_to_scene(d, _scene_id(i), image=image))
    return Storyboard(title=normalize_text(draft.title), language=settings.language,
                      style=style.id, aspect=settings.aspect or style.aspect, scenes=scenes)


def _chunks(text: str, max_words: int) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for sentence in _SENTENCES.split(text):
        words = sentence.split()
        if not words:
            continue
        if current and len(current) + len(words) > max_words:
            out.append(" ".join(current))
            current = []
        while len(words) > max_words * 1.5:
            out.append(" ".join(words[:max_words]))
            words = words[max_words:]
        current.extend(words)
    if current:
        out.append(" ".join(current))
    return out


def split_as_is(ir: ContentIR, sections: list[dict[str, Any]], style: StylePack,
                settings: GenerationSettings) -> Storyboard:
    """Keep the user's text verbatim: one title scene, then sections split into scenes of
    roughly ``style.script.scene_seconds`` each."""
    wps = float(style.section("pacing").get("words_per_second", 2.6))
    max_words = max(6, int(float(style.section("script").get("scene_seconds", 8)) * wps))
    scenes = [Scene(id="s1", narration=ir.title,
                    visual=Visual(type="title_card", title=ir.title))]
    for section in sections:
        images = list(section["images"])
        for k, chunk in enumerate(_chunks(section["text"], max_words) or [section["heading"]]):
            if images and k > 0:
                visual = Visual(type="image", title=section["heading"], image=images.pop(0))
            else:
                bullets = section["bullets"][:4] if k == 0 else []
                visual = Visual(type="text_card", title=section["heading"], bullets=bullets)
            scenes.append(Scene(id=_scene_id(len(scenes)), narration=chunk, visual=visual,
                                source_block_ids=section["block_ids"]))
        for image in images:
            scenes.append(Scene(id=_scene_id(len(scenes)), narration="",
                                visual=Visual(type="image", title=section["heading"],
                                              image=image), duration=3.0))
    return Storyboard(title=ir.title, language=settings.language, style=style.id,
                      aspect=settings.aspect or style.aspect, scenes=scenes[:200])


async def write_script(llm: ResolvedProvider | None, style: StylePack, ir: ContentIR,
                       settings: GenerationSettings, image_resolver=None) -> Storyboard:
    sections = ir_sections(ir, image_resolver)
    if not sections:
        raise ValueError("Tài liệu không có nội dung để tạo kịch bản")
    if settings.use_as_is or llm is None:
        return split_as_is(ir, sections, style, settings)
    payload = script_payload(ir, sections, style, settings)
    wps = payload["words_per_second"]
    variables = {
        "title": ir.title,
        "language": settings.language,
        "tone": settings.tone,
        "target_duration_s": settings.target_duration_s,
        "target_words": int(settings.target_duration_s * wps),
        "words_per_second": wps,
        "max_scenes": settings.max_scenes,
    }
    messages = [
        ChatMessage(role="system", content=style.render_prompt("system", **variables)),
        ChatMessage(role="user", content=style.render_prompt("script", payload=payload,
                                                              **variables)),
    ]
    draft = await complete_json(llm.provider, llm.ctx, messages, ScriptDraft, model=llm.model)
    images = [img for s in sections for img in s["images"]]
    return draft_to_storyboard(draft, style, settings, images)


async def regenerate_scene(llm: ResolvedProvider, style: StylePack, storyboard: Storyboard,
                           scene_id: str, instruction: str, settings: GenerationSettings) -> Scene:
    index = next((i for i, s in enumerate(storyboard.scenes) if s.id == scene_id), None)
    if index is None:
        raise KeyError(f"Không có cảnh {scene_id}")
    scene = storyboard.scenes[index]
    payload = {
        "task": "scene",
        "instruction": instruction,
        "scene": {"narration": scene.narration, "visual_type": scene.visual.type,
                  "title": scene.visual.title, "bullets": scene.visual.bullets,
                  "image_query": scene.visual.query},
    }
    variables = {
        "title": storyboard.title,
        "language": storyboard.language,
        "tone": settings.tone,
        "instruction": instruction,
        "previous": storyboard.scenes[index - 1].narration if index > 0 else "",
        "next": storyboard.scenes[index + 1].narration
        if index + 1 < len(storyboard.scenes) else "",
    }
    messages = [
        ChatMessage(role="system", content=style.render_prompt("system", **variables)),
        ChatMessage(role="user", content=style.render_prompt("scene", payload=payload,
                                                              **variables)),
    ]
    draft = await complete_json(llm.provider, llm.ctx, messages, DraftScene, model=llm.model)
    new = draft_scene_to_scene(draft, scene.id, scene.source_block_ids,
                               image=scene.visual.image)
    if scene.visual.type == "image" and scene.visual.image:
        new.visual = scene.visual.model_copy(update={"title": new.visual.title})
    new.overlays = scene.overlays
    new.duration = scene.duration
    return new
