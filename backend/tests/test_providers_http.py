"""HTTP behaviour of every provider, with respx-mocked endpoints (no network)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from videostudio.providers import llm, media, tts
from videostudio.providers.base import ChatMessage, MediaItem, ProviderContext
from videostudio.providers.errors import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderQuotaError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

MSGS = [ChatMessage(role="system", content="Bạn là trợ lý."),
        ChatMessage(role="user", content="Xin chào")]
SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}}
FAKE_MP3 = b"ID3fake-mp3-bytes"


@pytest.fixture(autouse=True)
def _fake_duration(monkeypatch):
    monkeypatch.setattr(tts, "media_duration", lambda path: 1.5)


def ctx(**config) -> ProviderContext:
    return ProviderContext(config={"timeout_s": 5, **config})


# ----------------------------------------------------------------------------- error matrix

LLM_CASES = [
    (llm.OpenAILLM(), {"api_key": "sk-test-123456", "model": "gpt-4o-mini"},
     "POST", "https://api.openai.com/v1/chat/completions"),
    (llm.OpenAICompatibleLLM(), {"base_url": "http://llm.local/v1", "model": "m",
                                 "api_key": "k-1234567890"},
     "POST", "http://llm.local/v1/chat/completions"),
    (llm.AnthropicLLM(), {"api_key": "sk-ant-123456789", "model": "claude-x"},
     "POST", "https://api.anthropic.com/v1/messages"),
    (llm.GeminiLLM(), {"api_key": "AIza-123456789", "model": "gemini-2.5-flash"},
     "POST", "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash"
             ":generateContent"),
    (llm.OllamaLLM(), {"base_url": "http://localhost:11434", "model": "qwen2.5:7b"},
     "POST", "http://localhost:11434/api/chat"),
]


@pytest.mark.parametrize(("provider", "config", "method", "url"), LLM_CASES,
                         ids=[c[0].id for c in LLM_CASES])
@pytest.mark.parametrize(("status", "body", "exc"), [
    (401, {"error": {"message": "Incorrect API key provided"}}, ProviderAuthError),
    (429, {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota"}},
     ProviderQuotaError),
])
async def test_llm_error_mapping(provider, config, method, url, status, body, exc):
    with respx.mock(assert_all_called=True) as mock:
        mock.request(method, url).mock(return_value=httpx.Response(status, json=body))
        with pytest.raises(exc) as info:
            await provider.complete(ctx(**config), MSGS)
    # secrets never leak into error messages
    for value in config.values():
        if "123456" in str(value):
            assert str(value) not in info.value.message


@pytest.mark.parametrize(("provider", "config", "method", "url"), LLM_CASES,
                         ids=[c[0].id for c in LLM_CASES])
async def test_llm_timeout(provider, config, method, url):
    with respx.mock() as mock:
        mock.request(method, url).mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(ProviderTimeoutError):
            await provider.complete(ctx(**config), MSGS)


async def test_llm_connect_error_is_unavailable():
    with respx.mock() as mock:
        mock.post("http://localhost:11434/api/chat").mock(side_effect=httpx.ConnectError("no"))
        with pytest.raises(ProviderUnavailableError):
            await llm.OllamaLLM().complete(ctx(model="x"), MSGS)


async def test_llm_missing_key_is_config_error():
    with pytest.raises(ProviderConfigError):
        await llm.OpenAILLM().complete(ctx(model="gpt-4o-mini"), MSGS)


# ----------------------------------------------------------------------------- LLM success


@respx.mock
async def test_openai_complete_json_schema_and_models():
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "model": "gpt-4o-mini", "usage": {"total_tokens": 9},
            "choices": [{"message": {"content": '{"a": 1}'}}]}))
    respx.get("https://api.openai.com/v1/models").mock(return_value=httpx.Response(
        200, json={"data": [{"id": "gpt-4o-mini"}, {"id": "whisper-1"}, {"id": "o3-mini"},
                            {"id": "gpt-4o-realtime-preview"}]}))
    c = ctx(api_key="sk-test-123456", model="gpt-4o-mini")
    res = await llm.OpenAILLM().complete(c, MSGS, json_schema=SCHEMA, temperature=0.3)
    assert res.text == '{"a": 1}'
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"] == SCHEMA
    assert sent["temperature"] == 0.3
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test-123456"
    models = [m.id for m in await llm.OpenAILLM().list_models(c)]
    assert models == ["gpt-4o-mini", "o3-mini"]
    test = await llm.OpenAILLM().test_connection(c)
    assert test.ok


@respx.mock
async def test_openai_compatible_json_modes():
    route = respx.post("http://llm.local/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}))
    p = llm.OpenAICompatibleLLM()
    await p.complete(ctx(base_url="http://llm.local/v1", model="m"), MSGS, json_schema=SCHEMA)
    assert json.loads(route.calls[0].request.content)["response_format"] == {
        "type": "json_object"}
    assert "authorization" not in route.calls[0].request.headers
    await p.complete(ctx(base_url="http://llm.local/v1", model="m", json_mode="none"), MSGS,
                     json_schema=SCHEMA)
    assert "response_format" not in json.loads(route.calls[1].request.content)


@respx.mock
async def test_anthropic_tool_use_output():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={
            "model": "claude-x",
            "content": [{"type": "tool_use", "name": "output", "input": {"a": 2}}]}))
    res = await llm.AnthropicLLM().complete(ctx(api_key="sk-ant-123456789", model="claude-x"),
                                            MSGS, json_schema=SCHEMA)
    assert json.loads(res.text) == {"a": 2}
    sent = json.loads(route.calls[0].request.content)
    assert sent["system"] == "Bạn là trợ lý."
    assert sent["tool_choice"] == {"type": "tool", "name": "output"}
    assert route.calls[0].request.headers["x-api-key"] == "sk-ant-123456789"


@respx.mock
async def test_gemini_json_mime_and_header_key():
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    ).mock(return_value=httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": '{"a": 3}'}]}}]}))
    res = await llm.GeminiLLM().complete(ctx(api_key="AIza-123456789", model="gemini-2.5-flash"),
                                         MSGS, json_schema=SCHEMA)
    assert res.text == '{"a": 3}'
    req = route.calls[0].request
    assert req.headers["x-goog-api-key"] == "AIza-123456789"
    assert "key=" not in str(req.url)  # key never in the URL
    body = json.loads(req.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "Bạn là trợ lý."


@respx.mock
async def test_ollama_format_schema_and_models():
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": '{"a": 4}'}}))
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]}))
    c = ctx(model="qwen2.5:7b")
    res = await llm.OllamaLLM().complete(c, MSGS, json_schema=SCHEMA)
    assert res.text == '{"a": 4}'
    assert json.loads(route.calls[0].request.content)["format"] == SCHEMA
    assert [m.id for m in await llm.OllamaLLM().list_models(c)] == ["qwen2.5:7b"]


# ----------------------------------------------------------------------------- TTS


TTS_CASES = [
    (tts.AzureTTS(), {"api_key": "azkey-123456789", "region": "southeastasia"},
     "POST", "https://southeastasia.tts.speech.microsoft.com/cognitiveservices/v1"),
    (tts.GoogleTTS(), {"api_key": "AIza-123456789"},
     "POST", "https://texttospeech.googleapis.com/v1beta1/text:synthesize"),
    (tts.ElevenLabsTTS(), {"api_key": "el-123456789", "default_voice": "voice1"},
     "POST", "https://api.elevenlabs.io/v1/text-to-speech/voice1/with-timestamps"),
    (tts.FPTTTS(), {"api_key": "fpt-123456789"}, "POST", "https://api.fpt.ai/hmi/tts/v5"),
    (tts.OpenAITTS(), {"api_key": "sk-test-123456"},
     "POST", "https://api.openai.com/v1/audio/speech"),
]


@pytest.mark.parametrize(("provider", "config", "method", "url"), TTS_CASES,
                         ids=[c[0].id for c in TTS_CASES])
@pytest.mark.parametrize(("status", "exc"), [(401, ProviderAuthError), (403, ProviderAuthError),
                                             (429, ProviderQuotaError),
                                             (402, ProviderQuotaError)])
async def test_tts_error_mapping(provider, config, method, url, status, exc, tmp_path):
    with respx.mock() as mock:
        mock.request(method, url).mock(return_value=httpx.Response(status, json={
            "error": {"message": "denied"}}))
        with pytest.raises(exc):
            await provider.synthesize(ctx(**config), "Xin chào", voice=None, lang="vi",
                                      out_path=tmp_path / "a.mp3")


@pytest.mark.parametrize(("provider", "config", "method", "url"), TTS_CASES,
                         ids=[c[0].id for c in TTS_CASES])
async def test_tts_timeout(provider, config, method, url, tmp_path):
    with respx.mock() as mock:
        mock.request(method, url).mock(side_effect=httpx.ConnectTimeout("slow"))
        with pytest.raises(ProviderTimeoutError):
            await provider.synthesize(ctx(**config), "Xin chào", voice=None, lang="vi",
                                      out_path=tmp_path / "a.mp3")


@respx.mock
async def test_azure_ssml_escaped_and_voices(tmp_path):
    route = respx.post("https://southeastasia.tts.speech.microsoft.com/cognitiveservices/v1")\
        .mock(return_value=httpx.Response(200, content=FAKE_MP3))
    respx.get("https://southeastasia.tts.speech.microsoft.com/cognitiveservices/voices/list")\
        .mock(return_value=httpx.Response(200, json=[
            {"ShortName": "vi-VN-HoaiMyNeural", "LocalName": "Hoài My", "Locale": "vi-VN",
             "Gender": "Female"},
            {"ShortName": "en-US-AvaNeural", "LocalName": "Ava", "Locale": "en-US"}]))
    c = ctx(api_key="azkey-123456789", region="southeastasia")
    out = await tts.AzureTTS().synthesize(c, "A < B & “chào”", voice="vi-VN-NamMinhNeural",
                                          lang="vi", out_path=tmp_path / "a.mp3")
    assert out.path.read_bytes() == FAKE_MP3 and out.duration_s == 1.5 and out.words == []
    req = route.calls[0].request
    ssml = req.content.decode("utf-8")
    assert "A &lt; B &amp; “chào”" in ssml and "vi-VN-NamMinhNeural" in ssml
    assert req.headers["ocp-apim-subscription-key"] == "azkey-123456789"
    voices = await tts.AzureTTS().list_voices(c, "vi")
    assert [v.id for v in voices] == ["vi-VN-HoaiMyNeural"]


@respx.mock
async def test_google_timepoints_to_words(tmp_path):
    route = respx.post("https://texttospeech.googleapis.com/v1beta1/text:synthesize").mock(
        return_value=httpx.Response(200, json={
            "audioContent": base64.b64encode(FAKE_MP3).decode(),
            "timepoints": [{"markName": "w0", "timeSeconds": 0.1},
                           {"markName": "w1", "timeSeconds": 0.5},
                           {"markName": "w2", "timeSeconds": 0.9}]}))
    out = await tts.GoogleTTS().synthesize(ctx(api_key="AIza-123456789"), "Xin chào bạn",
                                           voice="vi-VN-Wavenet-A", lang="vi",
                                           out_path=tmp_path / "g.mp3")
    assert [(w.text, w.start, w.end) for w in out.words] == [
        ("Xin", 0.1, 0.5), ("chào", 0.5, 0.9), ("bạn", 0.9, 1.5)]
    body = json.loads(route.calls[0].request.content)
    assert body["enableTimePointing"] == ["SSML_MARK"]
    assert '<mark name="w1"/>chào' in body["input"]["ssml"]
    assert body["voice"]["languageCode"] == "vi-VN"


@respx.mock
async def test_elevenlabs_character_alignment(tmp_path):
    text = "Xin chào"
    chars = list(text)
    starts = [i * 0.1 for i in range(len(chars))]
    respx.post("https://api.elevenlabs.io/v1/text-to-speech/v1/with-timestamps").mock(
        return_value=httpx.Response(200, json={
            "audio_base64": base64.b64encode(FAKE_MP3).decode(),
            "alignment": {"characters": chars, "character_start_times_seconds": starts,
                          "character_end_times_seconds": [s + 0.1 for s in starts]}}))
    out = await tts.ElevenLabsTTS().synthesize(ctx(api_key="el-123456789"), text, voice="v1",
                                               lang="vi", out_path=tmp_path / "e.mp3")
    assert [w.text for w in out.words] == ["Xin", "chào"]
    assert out.words[1].start == pytest.approx(0.4)


@respx.mock
async def test_fpt_async_polling(tmp_path, monkeypatch):
    monkeypatch.setattr(tts.FPTTTS, "poll_interval_s", 0.01)
    respx.post("https://api.fpt.ai/hmi/tts/v5").mock(return_value=httpx.Response(200, json={
        "async": "https://file.fpt.ai/a.mp3", "error": 0, "message": "ok"}))
    respx.get("https://file.fpt.ai/a.mp3").mock(side_effect=[
        httpx.Response(404), httpx.Response(200, content=b""),
        httpx.Response(200, content=FAKE_MP3)])
    out = await tts.FPTTTS().synthesize(ctx(api_key="fpt-123456789"), "Xin chào", voice="lannhi",
                                        lang="vi", out_path=tmp_path / "f.mp3")
    assert out.path.read_bytes() == FAKE_MP3


@respx.mock
async def test_fpt_api_error_message(tmp_path):
    respx.post("https://api.fpt.ai/hmi/tts/v5").mock(return_value=httpx.Response(200, json={
        "error": 1, "message": "Sai định dạng"}))
    with pytest.raises(Exception, match="Sai định dạng"):
        await tts.FPTTTS().synthesize(ctx(api_key="fpt-123456789"), "x", voice=None, lang="vi",
                                      out_path=tmp_path / "f.mp3")


@respx.mock
async def test_openai_tts(tmp_path):
    route = respx.post("https://api.openai.com/v1/audio/speech").mock(
        return_value=httpx.Response(200, content=FAKE_MP3))
    out = await tts.OpenAITTS().synthesize(ctx(api_key="sk-test-123456"), "Xin chào", voice=None,
                                           lang="vi", out_path=tmp_path / "o.mp3")
    assert out.path.read_bytes() == FAKE_MP3
    assert json.loads(route.calls[0].request.content)["voice"] == "nova"


async def test_mock_tts_writes_wav_with_words(tmp_path):
    out = await tts.MockTTS().synthesize(ctx(), "Một hai ba bốn năm.", voice=None, lang="vi",
                                         out_path=tmp_path / "m.wav")
    assert out.path.stat().st_size > 1000
    assert len(out.words) == 5 and out.words[-1].end <= out.duration_s


async def test_piper_reports_missing_package():
    if tts.PiperTTS.available():
        pytest.skip("piper installed")
    result = await tts.PiperTTS().test_connection(ctx())
    assert not result.ok and "piper" in result.message.lower()


class _FakeEdgeCommunicate:
    def __init__(self, text, voice, **kwargs):
        self.text, self.voice = text, voice

    async def stream(self):
        yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000,
               "text": "Xin"}
        yield {"type": "audio", "data": FAKE_MP3}
        yield {"type": "WordBoundary", "offset": 3_000_000, "duration": 2_000_000,
               "text": "chào"}


async def test_edge_tts_word_boundaries(tmp_path, monkeypatch):
    import edge_tts

    monkeypatch.setattr(edge_tts, "Communicate", _FakeEdgeCommunicate)
    out = await tts.EdgeTTS().synthesize(ctx(), "Xin chào", voice=None, lang="vi",
                                         out_path=tmp_path / "e.mp3")
    assert [(w.text, w.start) for w in out.words] == [("Xin", 0.1), ("chào", 0.3)]
    assert out.path.read_bytes() == FAKE_MP3


async def test_edge_tts_failure_is_unavailable(tmp_path, monkeypatch):
    import edge_tts

    class Broken(_FakeEdgeCommunicate):
        async def stream(self):
            raise edge_tts.exceptions.NoAudioReceived("no audio")
            yield  # pragma: no cover

    monkeypatch.setattr(edge_tts, "Communicate", Broken)
    with pytest.raises(ProviderUnavailableError):
        await tts.EdgeTTS().synthesize(ctx(), "Xin chào", voice=None, lang="vi",
                                       out_path=tmp_path / "e.mp3")


# ----------------------------------------------------------------------------- media


@respx.mock
async def test_pexels_search_download_and_errors(tmp_path):
    route = respx.get("https://api.pexels.com/v1/search").mock(return_value=httpx.Response(
        200, json={"photos": [{"id": 1, "width": 10, "height": 5, "photographer": "A",
                               "url": "https://pexels.com/p/1",
                               "src": {"large2x": "https://img.pexels.com/1.jpg",
                                       "medium": "https://img.pexels.com/1m.jpg"}}]}))
    respx.get("https://img.pexels.com/1.jpg").mock(return_value=httpx.Response(200,
                                                                               content=b"jpg"))
    c = ctx(api_key="px-123456789")
    items = await media.PexelsMedia().search(c, "mèo", orientation="portrait")
    assert items[0].url == "https://img.pexels.com/1.jpg" and items[0].author == "A"
    assert route.calls[0].request.headers["authorization"] == "px-123456789"
    assert route.calls[0].request.url.params["orientation"] == "portrait"
    dest = await media.PexelsMedia().download(c, items[0], tmp_path / "x.jpg")
    assert dest.read_bytes() == b"jpg"
    route.mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(ProviderAuthError):
        await media.PexelsMedia().search(c, "mèo")


async def test_mock_media_generates_image(tmp_path):
    from PIL import Image

    p = media.MockMedia()
    item = (await p.search(ctx(), "biển xanh", orientation="portrait"))[0]
    out = await p.download(ctx(), MediaItem(**{**item.model_dump(), "width": 90, "height": 160}),
                           tmp_path / "m.jpg")
    with Image.open(out) as img:
        assert img.size == (90, 160)


def test_every_provider_has_valid_schema():
    from videostudio.providers import builtin_providers

    seen = set()
    for p in builtin_providers():
        assert p.id not in seen
        seen.add(p.id)
        schema = p.config_schema()
        assert schema["type"] == "object"
        for key in schema.get("required", []):
            assert key in schema["properties"]
        for field in p.secret_fields():
            assert schema["properties"][field]["format"] == "password"
        assert p.describe()["name"]
    for cap in ("llm", "tts", "alignment", "media"):
        assert any(p.capability == cap and p.is_mock for p in builtin_providers()), cap


async def test_upstream_error_text_never_echoes_keys():
    body = {"error": {"message": "Incorrect API key provided: sk-e2e-t**************abcd. "
                                 "You can find your API key at https://platform.openai.com."}}
    with respx.mock() as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(401, json=body))
        with pytest.raises(ProviderAuthError) as info:
            await llm.OpenAILLM().complete(
                ctx(api_key="sk-e2e-test-1234567890abcd", model="gpt-4o-mini"), MSGS)
    message = info.value.message
    assert "sk-e2e" not in message and "abcd" not in message
    assert "Incorrect API key provided: ••••" in message
