"""Style packs, prompt templates (incl. user overrides) and the TTS cache."""

from __future__ import annotations

import jinja2
import pytest

from videostudio.pipeline.styles import FONTS_DIR, StyleLibrary
from videostudio.pipeline.tts_step import TTSCache, synthesize_narration, tts_cache_key
from videostudio.providers.alignment import MockAlignment
from videostudio.providers.base import ProviderContext, ResolvedProvider, TTSAudio
from videostudio.providers.errors import ProviderUnavailableError
from videostudio.providers.tts import MockTTS


def test_builtin_styles_and_fonts():
    lib = StyleLibrary()
    assert {"explainer_16x9", "short_9x16"} <= set(lib.ids())
    ex, short = lib.get("explainer_16x9"), lib.get("short_9x16")
    assert ex.aspect == "16:9" and ex.resolution() == (1920, 1080)
    assert short.aspect == "9:16" and short.resolution() == (1080, 1920)
    assert short.resolution(preview=True) == (540, 960)
    assert short.section("captions")["mode"] == "word_highlight"
    assert ex.section("captions")["mode"] == "line"
    for style in (ex, short):
        assert style.font_path("title").is_file()
        assert style.font_path("body").is_file()
    assert (FONTS_DIR / "OFL.txt").is_file()
    with pytest.raises(KeyError):
        lib.get("nope")


def test_prompt_rendering_vietnamese():
    style = StyleLibrary().get("explainer_16x9")
    system = style.render_prompt("system", language="vi", tone="vui vẻ")
    assert "tiếng Việt" in system and "vui vẻ" in system
    payload = {"task": "script", "title": "Quang hợp", "sections": [{"heading": "Lá cây"}]}
    prompt = style.render_prompt("script", payload=payload, title="Quang hợp", language="vi",
                                 tone="t", target_duration_s=60, target_words=156,
                                 words_per_second=2.6, max_scenes=8)
    assert "60 giây" in prompt and "156" in prompt and "8 cảnh" in prompt
    assert '"heading": "Lá cây"' in prompt  # payload embedded as JSON, unicode kept
    with pytest.raises(jinja2.UndefinedError):
        style.render_prompt("script", language="vi")


def test_user_override_prompt_and_new_style(tmp_path):
    user = tmp_path / "styles"
    (user / "explainer_16x9" / "prompts").mkdir(parents=True)
    (user / "explainer_16x9" / "prompts" / "system.j2").write_text(
        "Giọng {{ tone }} — bản tuỳ chỉnh", encoding="utf-8")
    (user / "explainer_16x9" / "style.yaml").write_text(
        "colors:\n  accent: '#ff0000'\n", encoding="utf-8")
    (user / "my_style" / "prompts").mkdir(parents=True)
    (user / "my_style" / "style.yaml").write_text(
        "name: Của tôi\naspect: '1:1'\n", encoding="utf-8")
    lib = StyleLibrary(user)
    ex = lib.get("explainer_16x9")
    assert ex.render_prompt("system", tone="nghiêm túc") == "Giọng nghiêm túc — bản tuỳ chỉnh"
    assert ex.prompt_source("system")[1] == "user"
    assert ex.prompt_source("script")[1] == "builtin"  # not overridden → falls back
    assert ex.section("colors")["accent"] == "#ff0000"
    assert ex.section("colors")["background"] == "#0f172a"  # merged, not replaced
    mine = lib.get("my_style")
    assert mine.name == "Của tôi" and mine.resolution() == (1080, 1080)
    assert "my_style" in [s["id"] for s in lib.list()]


class CountingTTS(MockTTS):
    calls = 0

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        type(self).calls += 1
        return await super().synthesize(ctx, text, voice=voice, lang=lang, out_path=out_path)


def _tts(**config) -> ResolvedProvider:
    return ResolvedProvider(provider=CountingTTS(), ctx=ProviderContext(config=config),
                            instance_id="i", instance_name="mock", voice=config.get("voice"))


async def test_tts_cache_hits_and_key_sensitivity(tmp_path):
    CountingTTS.calls = 0
    cache = TTSCache(tmp_path / "tts")
    a = await synthesize_narration(_tts(), "Xin chào các bạn.", "vi", cache)
    b = await synthesize_narration(_tts(), "Xin chào các bạn.", "vi", cache)
    assert CountingTTS.calls == 1 and not a.cached and b.cached
    assert a.key == b.key and b.audio_path.is_file()
    assert [w.text for w in b.words] == ["Xin", "chào", "các", "bạn."]
    # any change of text / voice / audio settings / language → new key
    keys = {a.key,
            tts_cache_key(_tts(), "Xin chào các bạn!", "vi"),
            tts_cache_key(_tts(voice="beep-high"), "Xin chào các bạn.", "vi"),
            tts_cache_key(_tts(words_per_second=4), "Xin chào các bạn.", "vi"),
            tts_cache_key(_tts(), "Xin chào các bạn.", "en")}
    assert len(keys) == 5
    # instance id / timeout do not affect the audio → same key
    other = _tts(timeout_s=99)
    other.instance_id = "another"
    assert tts_cache_key(other, "Xin chào các bạn.", "vi") == a.key
    # corrupted cache entry is ignored
    a.audio_path.write_bytes(b"")
    assert cache.get(a.key) is None


class NoTimestampsTTS(MockTTS):
    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        audio = await super().synthesize(ctx, text, voice=voice, lang=lang, out_path=out_path)
        return TTSAudio(path=audio.path, duration_s=audio.duration_s, words=[])


class BrokenAlignment(MockAlignment):
    async def align(self, ctx, **kw):
        raise ProviderUnavailableError("không có", provider="x")


async def test_alignment_fallback_chain(tmp_path):
    cache = TTSCache(tmp_path / "tts")
    tts = ResolvedProvider(provider=NoTimestampsTTS(), ctx=ProviderContext(config={}),
                           instance_id=None, instance_name="t")
    aligned = await synthesize_narration(
        tts, "một hai ba bốn", "vi", cache,
        ResolvedProvider(provider=MockAlignment(), ctx=ProviderContext(config={}),
                         instance_id=None, instance_name="a"))
    assert aligned.timing_source == "mock_alignment" and len(aligned.words) == 4
    logs = []
    fallback = await synthesize_narration(
        tts, "năm sáu bảy", "vi", cache,
        ResolvedProvider(provider=BrokenAlignment(), ctx=ProviderContext(config={}),
                         instance_id=None, instance_name="hỏng"),
        log_cb=lambda m, level="info": logs.append(m))
    assert fallback.timing_source == "proportional" and len(fallback.words) == 3
    assert logs and "hỏng" in logs[0]
