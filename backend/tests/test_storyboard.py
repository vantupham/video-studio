"""Storyboard models/schema sync, JSON repair, script writing and scene regeneration."""

from __future__ import annotations

import json
import re

import jsonschema
import pytest
from pydantic import ValidationError

from videostudio.ir import ContentIR
from videostudio.pipeline.models import (
    VISUAL_TYPES,
    GenerationSettings,
    Scene,
    ScriptDraft,
    Storyboard,
    Visual,
    storyboard_json_schema,
)
from videostudio.pipeline.script_writer import ir_sections, regenerate_scene, write_script
from videostudio.pipeline.styles import StyleLibrary
from videostudio.plugins.adapters.markdown import MarkdownAdapter
from videostudio.plugins.base import SourceRef
from videostudio.providers.base import (
    ChatMessage,
    LLMProvider,
    LLMResponse,
    ProviderContext,
    ResolvedProvider,
    TestResult,
)
from videostudio.providers.errors import ProviderOutputError
from videostudio.providers.llm import MockLLM
from videostudio.providers.llm_json import complete_json, extract_json, inline_refs

from .conftest import FIXTURES, REPO_ROOT

SCHEMA_FILE = REPO_ROOT / "packages" / "ir-schema" / "storyboard.schema.json"
TS_FILE = REPO_ROOT / "packages" / "ir-schema" / "ts" / "storyboard.ts"


def test_schema_file_in_sync_with_models():
    committed = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert committed == storyboard_json_schema(), (
        "Regenerate: python -m videostudio.pipeline.models > packages/ir-schema/"
        "storyboard.schema.json")


def test_ts_types_in_sync():
    ts = TS_FILE.read_text(encoding="utf-8")
    m = re.search(r"VISUAL_TYPES = \[(.*?)\] as const", ts)
    assert m and re.findall(r'"([a-z_]+)"', m.group(1)) == list(VISUAL_TYPES)
    schema = storyboard_json_schema()
    for model in ("Visual", "Scene", "Overlay"):
        for field in schema["$defs"][model]["properties"]:
            assert re.search(rf"\b{field}\??:", ts), f"{model}.{field} missing in TS"
    for field in schema["properties"]:
        assert re.search(rf"\b{field}\??:", ts), f"Storyboard.{field} missing in TS"


def _sb(**kw):
    base = {"title": "Bài học", "scenes": [{"id": "s1", "narration": "Xin chào."}]}
    base.update(kw)
    return base


def test_storyboard_validation():
    sb = Storyboard.model_validate(_sb())
    assert sb.scenes[0].visual.type == "text_card" and sb.scenes[0].duration == "auto"
    jsonschema.validate(sb.model_dump(mode="json"), storyboard_json_schema())
    bad = [
        _sb(scenes=[]),
        _sb(scenes=[{"id": "s1"}, {"id": "s1"}]),
        _sb(scenes=[{"id": "s 1"}]),
        _sb(scenes=[{"id": "s1", "duration": 0.1}]),
        _sb(scenes=[{"id": "s1", "visual": {"type": "video"}}]),
        _sb(scenes=[{"id": "s1", "visual": {"color": "red"}}]),
        _sb(aspect="4:3"),
        _sb(extra=1),
    ]
    for case in bad:
        with pytest.raises(ValidationError):
            Storyboard.model_validate(case)


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Đây:\n```json\n{"a": "á"}\n```') == {"a": "á"}
    assert extract_json('blah {"a": [1, 2]} trailing') == {"a": [1, 2]}
    with pytest.raises(json.JSONDecodeError):
        extract_json("không có json")


def test_inline_refs_removes_defs():
    schema = inline_refs(ScriptDraft.model_json_schema())
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    assert schema["properties"]["scenes"]["items"]["properties"]["narration"]


class ScriptedLLM(LLMProvider):
    id = "scripted"
    name = "Scripted"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    def config_schema(self):
        return {"type": "object", "properties": {}}

    async def test_connection(self, ctx):
        return TestResult(ok=True, message="ok")

    async def complete(self, ctx, messages, **kw):
        self.calls.append(messages)
        return LLMResponse(text=self.replies.pop(0))


GOOD = json.dumps({"title": "T", "scenes": [{"narration": "Một hai ba.",
                                             "visual_type": "title_card"}]})


async def test_complete_json_first_try():
    llm = ScriptedLLM([GOOD])
    draft = await complete_json(llm, ProviderContext(config={}),
                                [ChatMessage(role="system", content="s"),
                                 ChatMessage(role="user", content="u")], ScriptDraft)
    assert draft.title == "T" and len(llm.calls) == 1
    assert "JSON Schema" in llm.calls[0][0].content


async def test_complete_json_repairs_once():
    llm = ScriptedLLM(['{"title": "T", "scenes": []}', GOOD])
    draft = await complete_json(llm, ProviderContext(config={}),
                                [ChatMessage(role="user", content="u")], ScriptDraft)
    assert draft.scenes[0].narration == "Một hai ba."
    repair = llm.calls[1]
    assert repair[-2].role == "assistant" and "scenes" in repair[-1].content


async def test_complete_json_gives_up_after_one_repair():
    llm = ScriptedLLM(["không phải json", "vẫn không phải"])
    with pytest.raises(ProviderOutputError):
        await complete_json(llm, ProviderContext(config={}),
                            [ChatMessage(role="user", content="u")], ScriptDraft)
    assert len(llm.calls) == 2


async def _ir() -> ContentIR:
    path = FIXTURES / "bài học tiếng việt.md"
    src = SourceRef(type="file", uri=path.name, path=path, filename=path.name)
    from videostudio.plugins.base import JobContext

    return await MarkdownAdapter().ingest(src, JobContext(job_id="t", project_dir=None))


def _mock(**config) -> ResolvedProvider:
    return ResolvedProvider(provider=MockLLM(), ctx=ProviderContext(config=config),
                            instance_id=None, instance_name="mock")


@pytest.mark.parametrize("style_id", ["explainer_16x9", "short_9x16"])
async def test_write_script_with_mock_llm(style_id):
    ir = await _ir()
    style = StyleLibrary().get(style_id)
    gen = GenerationSettings(style=style_id, target_duration_s=30, max_scenes=5)
    sb = await write_script(_mock(), style, ir, gen)
    assert sb.style == style_id and sb.aspect == style.aspect
    assert sb.scenes[0].visual.type == "title_card"
    assert 2 <= len(sb.scenes) <= 7
    assert all(s.narration for s in sb.scenes)
    assert [s.id for s in sb.scenes] == [f"s{i + 1}" for i in range(len(sb.scenes))]
    words = sum(len(s.narration.split()) for s in sb.scenes)
    assert words < 30 * 3.2  # roughly respects the target duration


async def test_write_script_repair_path_with_mock():
    ir = await _ir()
    sb = await write_script(_mock(fail_first_json=True), StyleLibrary().get("explainer_16x9"),
                            ir, GenerationSettings())
    assert sb.scenes


async def test_use_as_is_keeps_text_verbatim():
    ir = await _ir()
    style = StyleLibrary().get("explainer_16x9")
    sb = await write_script(None, style, ir, GenerationSettings(use_as_is=True))
    original = " ".join(s["text"] for s in ir_sections(ir))
    narration = " ".join(s.narration for s in sb.scenes[1:])
    assert narration.split() == original.split()
    max_words = int(style.section("script")["scene_seconds"] * 2.6 * 1.5) + 1
    assert all(len(s.narration.split()) <= max_words for s in sb.scenes)


def test_ir_sections_punctuation_and_repeated_heading():
    ir = ContentIR.model_validate({
        "title": "Uống nước", "source": {"type": "markdown", "uri": "a.md"},
        "sections": [{"id": "a", "title": "Cần uống bao nhiêu?", "blocks": [
            {"kind": "heading", "level": 2, "text": "Cần uống bao nhiêu?"},
            {"kind": "heading", "level": 3, "text": "Mẹo nhỏ"},
            {"kind": "list", "ordered": False,
             "items": [{"text": "Uống đủ nước!"}, {"text": "Hạn chế nước ngọt"}]},
        ]}],
    })
    [section] = ir_sections(ir)
    assert section["heading"] == "Cần uống bao nhiêu?"
    assert section["text"] == "Mẹo nhỏ. Uống đủ nước! Hạn chế nước ngọt."


async def test_regenerate_single_scene_only_changes_that_scene():
    sb = Storyboard(title="T", scenes=[
        Scene(id="s1", narration="Câu một. Câu hai.", visual=Visual(type="title_card",
                                                                       title="A")),
        Scene(id="s2", narration="Cảnh hai.", duration=4.0),
    ])
    new = await regenerate_scene(_mock(), StyleLibrary().get("explainer_16x9"), sb, "s2",
                                 "thêm ví dụ", GenerationSettings())
    assert new.id == "s2" and "thêm ví dụ" in new.narration and new.duration == 4.0
    short = await regenerate_scene(_mock(), StyleLibrary().get("explainer_16x9"), sb, "s1",
                                   "viết ngắn hơn", GenerationSettings())
    assert short.narration == "Câu một." and short.visual.title == "A"
    with pytest.raises(KeyError):
        await regenerate_scene(_mock(), StyleLibrary().get("explainer_16x9"), sb, "zz", "",
                               GenerationSettings())
