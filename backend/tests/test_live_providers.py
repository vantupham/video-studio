"""Live smoke tests against real provider APIs.

Each test runs only when its credentials are present in the environment, e.g.::

    OPENAI_API_KEY=… ANTHROPIC_API_KEY=… GEMINI_API_KEY=… AZURE_SPEECH_KEY=…
    AZURE_SPEECH_REGION=southeastasia GOOGLE_TTS_API_KEY=… ELEVENLABS_API_KEY=…
    ELEVENLABS_VOICE_ID=… FPT_AI_KEY=… PEXELS_API_KEY=… OLLAMA_URL=http://localhost:11434
    VIDEOSTUDIO_LIVE_EDGE=1   # free Edge TTS (needs internet)

    uv run pytest tests/test_live_providers.py -v
"""

from __future__ import annotations

import os

import pytest

from videostudio.pipeline.models import ScriptDraft
from videostudio.providers import llm, media, tts
from videostudio.providers.base import ChatMessage, ProviderContext
from videostudio.providers.llm_json import complete_json

pytestmark = pytest.mark.live


def env(*names: str) -> dict[str, str]:
    values = {n: os.environ.get(n, "") for n in names}
    if not all(values.values()):
        pytest.skip(f"missing {', '.join(n for n, v in values.items() if not v)}")
    return values


MSGS = [ChatMessage(role="system", content="Bạn là biên kịch video."),
        ChatMessage(role="user", content="Viết kịch bản 2 cảnh ngắn về quả chuối.")]

LLM_LIVE = [
    ("OPENAI_API_KEY", llm.OpenAILLM(), {"model": "gpt-4o-mini"}),
    ("ANTHROPIC_API_KEY", llm.AnthropicLLM(), {"model": "claude-sonnet-4-5"}),
    ("GEMINI_API_KEY", llm.GeminiLLM(), {"model": "gemini-2.5-flash"}),
]


@pytest.mark.parametrize(("var", "provider", "config"), LLM_LIVE, ids=[c[0] for c in LLM_LIVE])
async def test_llm_live(var, provider, config):
    key = env(var)[var]
    ctx = ProviderContext(config={"api_key": key, "timeout_s": 120, **config})
    assert (await provider.test_connection(ctx)).ok
    draft = await complete_json(provider, ctx, MSGS, ScriptDraft)
    assert draft.scenes


async def test_ollama_live():
    url = env("OLLAMA_URL")["OLLAMA_URL"]
    p = llm.OllamaLLM()
    ctx = ProviderContext(config={"base_url": url, "timeout_s": 300,
                                  "model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")})
    assert (await p.test_connection(ctx)).ok


TTS_LIVE = [
    (("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"), tts.AzureTTS(),
     lambda e: {"api_key": e["AZURE_SPEECH_KEY"], "region": e["AZURE_SPEECH_REGION"]}),
    (("GOOGLE_TTS_API_KEY",), tts.GoogleTTS(), lambda e: {"api_key": e["GOOGLE_TTS_API_KEY"]}),
    (("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"), tts.ElevenLabsTTS(),
     lambda e: {"api_key": e["ELEVENLABS_API_KEY"], "default_voice": e["ELEVENLABS_VOICE_ID"]}),
    (("FPT_AI_KEY",), tts.FPTTTS(), lambda e: {"api_key": e["FPT_AI_KEY"]}),
    (("OPENAI_API_KEY",), tts.OpenAITTS(), lambda e: {"api_key": e["OPENAI_API_KEY"]}),
    (("VIDEOSTUDIO_LIVE_EDGE",), tts.EdgeTTS(), lambda e: {}),
]


@pytest.mark.parametrize(("vars_", "provider", "make"), TTS_LIVE,
                         ids=[c[1].id for c in TTS_LIVE])
async def test_tts_live(vars_, provider, make, tmp_path):
    ctx = ProviderContext(config={"timeout_s": 90, **make(env(*vars_))})
    out = await provider.synthesize(ctx, "Xin chào, đây là bài kiểm tra giọng đọc tiếng Việt.",
                                    voice=provider.default_voice(ctx), lang="vi",
                                    out_path=tmp_path / f"live{provider.audio_ext}")
    assert out.path.stat().st_size > 1000 and out.duration_s > 1
    voices = await provider.list_voices(ctx, "vi")
    assert voices


async def test_pexels_live(tmp_path):
    key = env("PEXELS_API_KEY")["PEXELS_API_KEY"]
    ctx = ProviderContext(config={"api_key": key})
    items = await media.PexelsMedia().search(ctx, "rice field", limit=1)
    assert items
    assert (await media.PexelsMedia().download(ctx, items[0], tmp_path / "p.jpg")).is_file()
