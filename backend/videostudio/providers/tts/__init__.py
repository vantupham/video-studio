"""Text-to-speech providers."""

from __future__ import annotations

import asyncio
import base64
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar
from xml.sax.saxutils import escape

from ...media.audio import media_duration, write_tone_wav
from ..alignment.text import group_chars_to_words, proportional_timings, tokenize
from ..base import (
    ProviderContext,
    TestResult,
    TTSAudio,
    TTSProvider,
    VoiceInfo,
    WordTiming,
    secret_field,
    timeout_field,
)
from ..errors import (
    ProviderConfigError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    client,
    send,
)

log = logging.getLogger(__name__)

LANG_LOCALES = {"vi": "vi-VN", "en": "en-US"}


def _locale(lang: str) -> str:
    return LANG_LOCALES.get(lang, lang if "-" in lang else f"{lang}-{lang.upper()}")


def _key(ctx: ProviderContext, provider: str) -> str:
    key = ctx.get("api_key")
    if not key:
        raise ProviderConfigError(f"{provider}: chưa cấu hình API key", provider=provider)
    return str(key)


async def _sample(provider: TTSProvider, ctx: ProviderContext) -> TestResult:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"test{provider.audio_ext}"
        audio = await provider.synthesize(ctx, "Xin chào.", voice=provider.default_voice(ctx),
                                          lang="vi", out_path=out)
        return TestResult(ok=True, message=f"Tổng hợp thử thành công ({audio.duration_s:.1f}s)",
                          details={"duration_s": audio.duration_s,
                                   "word_timestamps": bool(audio.words)})


# ----------------------------------------------------------------------------- Edge (free)

EDGE_VI_VOICES = [
    VoiceInfo(id="vi-VN-HoaiMyNeural", name="Hoài My (nữ)", lang="vi-VN", gender="Female"),
    VoiceInfo(id="vi-VN-NamMinhNeural", name="Nam Minh (nam)", lang="vi-VN", gender="Male"),
]


class EdgeTTS(TTSProvider):
    id: ClassVar[str] = "edge_tts"
    name: ClassVar[str] = "Microsoft Edge TTS (miễn phí)"
    description: ClassVar[str] = (
        "Dùng dịch vụ đọc văn bản của trình duyệt Edge. Miễn phí, không cần key, có mốc thời "
        "gian từng từ. KHÔNG chính thức — có thể ngừng hoạt động bất cứ lúc nào.")
    docs_url: ClassVar[str] = "https://github.com/rany2/edge-tts"
    unofficial: ClassVar[bool] = True

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "default_voice": {"type": "string", "title": "Giọng mặc định",
                                  "default": "vi-VN-HoaiMyNeural", "x-source": "voices"},
                "rate": {"type": "string", "title": "Tốc độ", "default": "+0%",
                         "description": "Ví dụ: +10%, -5%"},
                "pitch": {"type": "string", "title": "Cao độ", "default": "+0Hz"},
                "timeout_s": timeout_field(60),
            },
        }

    async def list_voices(self, ctx, lang=None):
        try:
            import edge_tts

            voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=ctx.timeout)
        except Exception as exc:  # network / service changes
            log.info("edge-tts list_voices failed: %s", type(exc).__name__)
            return [v for v in EDGE_VI_VOICES if not lang or v.lang.startswith(lang)]
        out = []
        for v in voices:
            locale = v.get("Locale", "")
            if lang and not locale.lower().startswith(lang.lower()):
                continue
            out.append(VoiceInfo(id=v["ShortName"], name=v.get("FriendlyName", v["ShortName"]),
                                 lang=locale, gender=v.get("Gender")))
        return out

    def default_voice(self, ctx):
        return ctx.get("default_voice", "vi-VN-HoaiMyNeural")

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        import edge_tts

        voice = voice or self.default_voice(ctx)
        communicate = edge_tts.Communicate(
            text, voice, rate=str(ctx.get("rate", "+0%")), pitch=str(ctx.get("pitch", "+0Hz")),
            boundary="WordBoundary", receive_timeout=int(ctx.timeout))
        words: list[WordTiming] = []
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(out_path, "wb") as fh:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / 1e7
                        words.append(WordTiming(text=chunk["text"], start=start,
                                                end=start + chunk["duration"] / 1e7))
        except TimeoutError as exc:
            raise ProviderTimeoutError(f"{self.name}: quá thời gian chờ", provider=self.id) \
                from exc
        except Exception as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderUnavailableError(
                f"{self.name}: không tổng hợp được giọng nói ({type(exc).__name__}: {exc})",
                provider=self.id) from exc
        if out_path.stat().st_size == 0:
            raise ProviderUnavailableError(f"{self.name}: dịch vụ không trả âm thanh",
                                           provider=self.id)
        return TTSAudio(path=out_path, duration_s=media_duration(out_path), words=words)

    async def test_connection(self, ctx):
        return await _sample(self, ctx)


# ----------------------------------------------------------------------------- Azure

AZURE_VI_VOICES = [
    VoiceInfo(id="vi-VN-HoaiMyNeural", name="Hoài My (nữ)", lang="vi-VN", gender="Female"),
    VoiceInfo(id="vi-VN-NamMinhNeural", name="Nam Minh (nam)", lang="vi-VN", gender="Male"),
]


class AzureTTS(TTSProvider):
    id: ClassVar[str] = "azure_tts"
    name: ClassVar[str] = "Azure AI Speech"
    description: ClassVar[str] = (
        "Giọng Neural của Microsoft Azure (Hoài My, Nam Minh). Gói miễn phí F0: 0,5 triệu ký "
        "tự/tháng. REST API không trả mốc thời gian từng từ — phụ đề dùng bộ căn thời gian.")
    docs_url: ClassVar[str] = "https://portal.azure.com/#create/Microsoft.CognitiveServicesSpeechServices"

    def config_schema(self):
        return {
            "type": "object",
            "required": ["api_key", "region"],
            "properties": {
                "api_key": secret_field("Khoá (Key 1 hoặc Key 2)"),
                "region": {"type": "string", "title": "Vùng (region)", "default": "southeastasia",
                           "description": "Ví dụ: southeastasia, eastasia, eastus"},
                "endpoint": {"type": "string", "title": "Endpoint tuỳ chỉnh (tuỳ chọn)"},
                "default_voice": {"type": "string", "title": "Giọng mặc định",
                                  "default": "vi-VN-HoaiMyNeural", "x-source": "voices"},
                "rate": {"type": "string", "title": "Tốc độ (prosody rate)", "default": "+0%"},
                "timeout_s": timeout_field(60),
            },
        }

    def _host(self, ctx):
        if ctx.get("endpoint"):
            return str(ctx.get("endpoint")).rstrip("/")
        region = ctx.get("region")
        if not region:
            raise ProviderConfigError(f"{self.name}: chưa cấu hình region", provider=self.id)
        return f"https://{region}.tts.speech.microsoft.com"

    def _headers(self, ctx):
        return {"Ocp-Apim-Subscription-Key": _key(ctx, self.name), "User-Agent": "VideoStudio"}

    async def list_voices(self, ctx, lang=None):
        async with client(ctx.timeout) as http:
            r = await send(http, "GET", f"{self._host(ctx)}/cognitiveservices/voices/list",
                           provider=self.name, headers=self._headers(ctx))
        return [
            VoiceInfo(id=v["ShortName"], name=v.get("LocalName") or v.get("DisplayName", ""),
                      lang=v.get("Locale", ""), gender=v.get("Gender"))
            for v in r.json() if not lang or v.get("Locale", "").lower().startswith(lang.lower())
        ]

    def default_voice(self, ctx):
        return ctx.get("default_voice", "vi-VN-HoaiMyNeural")

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        voice = voice or self.default_voice(ctx)
        locale = "-".join(voice.split("-")[:2]) if voice.count("-") >= 2 else _locale(lang)
        ssml = (f"<speak version='1.0' xml:lang='{locale}'><voice name='{escape(voice)}'>"
                f"<prosody rate='{escape(str(ctx.get('rate', '+0%')))}'>{escape(text)}"
                "</prosody></voice></speak>")
        headers = {**self._headers(ctx), "Content-Type": "application/ssml+xml",
                   "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3"}
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", f"{self._host(ctx)}/cognitiveservices/v1",
                           provider=self.name, headers=headers,
                           content=ssml.encode("utf-8"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return TTSAudio(path=out_path, duration_s=media_duration(out_path), words=[])

    async def test_connection(self, ctx):
        voices = await self.list_voices(ctx, "vi")
        return TestResult(ok=True, message=f"Kết nối thành công ({len(voices)} giọng tiếng Việt)")


# ----------------------------------------------------------------------------- Google


class GoogleTTS(TTSProvider):
    id: ClassVar[str] = "google_tts"
    name: ClassVar[str] = "Google Cloud Text-to-Speech"
    description: ClassVar[str] = (
        "Giọng WaveNet / Neural2 / Chirp của Google Cloud. Có mốc thời gian từng từ qua SSML "
        "mark. Cần bật Text-to-Speech API và tạo API key.")
    docs_url: ClassVar[str] = "https://console.cloud.google.com/apis/library/texttospeech.googleapis.com"

    def config_schema(self):
        return {
            "type": "object",
            "required": ["api_key"],
            "properties": {
                "api_key": secret_field("API key"),
                "default_voice": {"type": "string", "title": "Giọng mặc định",
                                  "default": "vi-VN-Wavenet-A", "x-source": "voices"},
                "speaking_rate": {"type": "number", "title": "Tốc độ đọc", "default": 1.0,
                                  "minimum": 0.25, "maximum": 4.0},
                "base_url": {"type": "string", "title": "Base URL",
                             "default": "https://texttospeech.googleapis.com"},
                "timeout_s": timeout_field(60),
            },
        }

    def _base(self, ctx):
        return str(ctx.get("base_url", "https://texttospeech.googleapis.com")).rstrip("/")

    def _headers(self, ctx):
        return {"X-Goog-Api-Key": _key(ctx, self.name), "Content-Type": "application/json"}

    async def list_voices(self, ctx, lang=None):
        params = {"languageCode": _locale(lang)} if lang else {}
        async with client(ctx.timeout) as http:
            r = await send(http, "GET", f"{self._base(ctx)}/v1/voices", provider=self.name,
                           headers=self._headers(ctx), params=params)
        return [VoiceInfo(id=v["name"], name=v["name"],
                          lang=(v.get("languageCodes") or [""])[0],
                          gender=(v.get("ssmlGender") or "").title() or None)
                for v in r.json().get("voices", [])]

    def default_voice(self, ctx):
        return ctx.get("default_voice", "vi-VN-Wavenet-A")

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        voice = voice or self.default_voice(ctx)
        tokens = tokenize(text)
        ssml = "<speak>" + " ".join(f'<mark name="w{i}"/>{escape(t)}'
                                    for i, t in enumerate(tokens)) + "</speak>"
        locale = "-".join(voice.split("-")[:2]) if voice.count("-") >= 2 else _locale(lang)
        body = {
            "input": {"ssml": ssml},
            "voice": {"languageCode": locale, "name": voice},
            "audioConfig": {"audioEncoding": "MP3",
                            "speakingRate": float(ctx.get("speaking_rate", 1.0))},
            "enableTimePointing": ["SSML_MARK"],
        }
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", f"{self._base(ctx)}/v1beta1/text:synthesize",
                           provider=self.name, headers=self._headers(ctx), json=body)
        data = r.json()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(data["audioContent"]))
        duration = media_duration(out_path)
        marks = {tp["markName"]: float(tp.get("timeSeconds", 0))
                 for tp in data.get("timepoints", [])}
        words: list[WordTiming] = []
        starts = [marks.get(f"w{i}") for i in range(len(tokens))]
        if all(s is not None for s in starts):
            for i, token in enumerate(tokens):
                end = starts[i + 1] if i + 1 < len(tokens) else duration
                words.append(WordTiming(text=token, start=starts[i], end=max(starts[i], end)))
        return TTSAudio(path=out_path, duration_s=duration, words=words)

    async def test_connection(self, ctx):
        voices = await self.list_voices(ctx, "vi")
        return TestResult(ok=True, message=f"Kết nối thành công ({len(voices)} giọng tiếng Việt)")


# ----------------------------------------------------------------------------- ElevenLabs


class ElevenLabsTTS(TTSProvider):
    id: ClassVar[str] = "elevenlabs"
    name: ClassVar[str] = "ElevenLabs"
    description: ClassVar[str] = (
        "Giọng đọc tự nhiên, hỗ trợ tiếng Việt với model Flash/Turbo v2.5. Có mốc thời gian "
        "từng ký tự (endpoint with-timestamps).")
    docs_url: ClassVar[str] = "https://elevenlabs.io/app/settings/api-keys"

    def config_schema(self):
        return {
            "type": "object",
            "required": ["api_key", "default_voice"],
            "properties": {
                "api_key": secret_field("API key"),
                "default_voice": {"type": "string", "title": "Voice ID",
                                  "x-source": "voices"},
                "model": {"type": "string", "title": "Model", "default": "eleven_flash_v2_5",
                          "enum": ["eleven_flash_v2_5", "eleven_turbo_v2_5",
                                   "eleven_multilingual_v2", "eleven_v3"]},
                "base_url": {"type": "string", "title": "Base URL",
                             "default": "https://api.elevenlabs.io"},
                "timeout_s": timeout_field(120),
            },
        }

    def _base(self, ctx):
        return str(ctx.get("base_url", "https://api.elevenlabs.io")).rstrip("/")

    def _headers(self, ctx):
        return {"xi-api-key": _key(ctx, self.name)}

    async def list_voices(self, ctx, lang=None):
        async with client(ctx.timeout) as http:
            r = await send(http, "GET", f"{self._base(ctx)}/v1/voices", provider=self.name,
                           headers=self._headers(ctx))
        out = []
        for v in r.json().get("voices", []):
            labels = v.get("labels") or {}
            out.append(VoiceInfo(id=v["voice_id"], name=v.get("name", v["voice_id"]),
                                 lang=labels.get("language", ""), gender=labels.get("gender"),
                                 description=labels.get("description")))
        return out

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        voice = voice or self.default_voice(ctx)
        if not voice:
            raise ProviderConfigError(f"{self.name}: chưa chọn Voice ID", provider=self.id)
        model = str(ctx.get("model", "eleven_flash_v2_5"))
        body: dict[str, Any] = {"text": text, "model_id": model}
        if model.endswith("v2_5"):
            body["language_code"] = lang.split("-")[0]
        async with client(ctx.timeout) as http:
            r = await send(http, "POST",
                           f"{self._base(ctx)}/v1/text-to-speech/{voice}/with-timestamps",
                           provider=self.name, headers=self._headers(ctx), json=body,
                           params={"output_format": "mp3_44100_128"})
        data = r.json()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(data["audio_base64"]))
        align = data.get("alignment") or {}
        words = group_chars_to_words(align.get("characters", []),
                                     align.get("character_start_times_seconds", []),
                                     align.get("character_end_times_seconds", []))
        return TTSAudio(path=out_path, duration_s=media_duration(out_path), words=words)

    async def test_connection(self, ctx):
        voices = await self.list_voices(ctx)
        return TestResult(ok=True, message=f"Kết nối thành công ({len(voices)} giọng)")


# ----------------------------------------------------------------------------- FPT.AI

FPT_VOICES = [
    VoiceInfo(id="banmai", name="Ban Mai (nữ miền Bắc)", lang="vi-VN", gender="Female"),
    VoiceInfo(id="thuminh", name="Thu Minh (nữ miền Bắc)", lang="vi-VN", gender="Female"),
    VoiceInfo(id="leminh", name="Lê Minh (nam miền Bắc)", lang="vi-VN", gender="Male"),
    VoiceInfo(id="giahuy", name="Gia Huy (nam miền Trung)", lang="vi-VN", gender="Male"),
    VoiceInfo(id="myan", name="Mỹ An (nữ miền Trung)", lang="vi-VN", gender="Female"),
    VoiceInfo(id="lannhi", name="Lan Nhi (nữ miền Nam)", lang="vi-VN", gender="Female"),
    VoiceInfo(id="linhsan", name="Linh San (nữ miền Nam)", lang="vi-VN", gender="Female"),
]


class FPTTTS(TTSProvider):
    id: ClassVar[str] = "fpt_tts"
    name: ClassVar[str] = "FPT.AI Text to Speech"
    description: ClassVar[str] = (
        "Giọng Việt ba miền của FPT.AI. API bất đồng bộ: trả về link mp3 sau vài giây. "
        "Không có mốc thời gian từng từ.")
    docs_url: ClassVar[str] = "https://console.fpt.ai/"
    poll_interval_s: ClassVar[float] = 1.5

    def config_schema(self):
        return {
            "type": "object",
            "required": ["api_key"],
            "properties": {
                "api_key": secret_field("API key"),
                "default_voice": {"type": "string", "title": "Giọng mặc định", "default": "banmai",
                                  "enum": [v.id for v in FPT_VOICES],
                                  "x-enumLabels": {v.id: v.name for v in FPT_VOICES}},
                "speed": {"type": "integer", "title": "Tốc độ (-3 … 3)", "default": 0,
                          "minimum": -3, "maximum": 3},
                "endpoint": {"type": "string", "title": "Endpoint",
                             "default": "https://api.fpt.ai/hmi/tts/v5"},
                "timeout_s": timeout_field(90),
            },
        }

    async def list_voices(self, ctx, lang=None):
        return [v for v in FPT_VOICES if not lang or v.lang.startswith(lang)]

    def default_voice(self, ctx):
        return ctx.get("default_voice", "banmai")

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        headers = {"api-key": _key(ctx, self.name), "voice": voice or self.default_voice(ctx),
                   "speed": str(ctx.get("speed", 0)), "format": "mp3"}
        endpoint = str(ctx.get("endpoint", "https://api.fpt.ai/hmi/tts/v5"))
        deadline = time.monotonic() + ctx.timeout
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", endpoint, provider=self.name, headers=headers,
                           content=text.encode("utf-8"))
            data = r.json()
            if data.get("error") not in (0, None) or not data.get("async"):
                raise ProviderError(f"{self.name}: {data.get('message', 'lỗi không xác định')}",
                                    provider=self.id)
            url = data["async"]
            while True:
                resp = await http.get(url)
                if resp.status_code == 200 and resp.content:
                    break
                if time.monotonic() > deadline:
                    raise ProviderTimeoutError(f"{self.name}: file âm thanh chưa sẵn sàng",
                                               provider=self.id)
                await asyncio.sleep(self.poll_interval_s)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)
        return TTSAudio(path=out_path, duration_s=media_duration(out_path), words=[])

    async def test_connection(self, ctx):
        return await _sample(self, ctx)


# ----------------------------------------------------------------------------- OpenAI TTS

OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage",
                 "shimmer", "verse"]


class OpenAITTS(TTSProvider):
    id: ClassVar[str] = "openai_tts"
    name: ClassVar[str] = "OpenAI TTS"
    description: ClassVar[str] = "Giọng đa ngôn ngữ của OpenAI (đọc được tiếng Việt, giọng "
    "hơi lơ lớ). Không có mốc thời gian từng từ."
    docs_url: ClassVar[str] = "https://platform.openai.com/api-keys"

    def config_schema(self):
        return {
            "type": "object",
            "required": ["api_key"],
            "properties": {
                "api_key": secret_field("API key"),
                "model": {"type": "string", "title": "Model", "default": "gpt-4o-mini-tts",
                          "enum": ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"]},
                "default_voice": {"type": "string", "title": "Giọng mặc định", "default": "nova",
                                  "enum": OPENAI_VOICES},
                "instructions": {"type": "string", "title": "Hướng dẫn giọng đọc (chỉ "
                                 "gpt-4o-mini-tts)", "x-multiline": True},
                "base_url": {"type": "string", "title": "Base URL",
                             "default": "https://api.openai.com/v1"},
                "timeout_s": timeout_field(120),
            },
        }

    def _base(self, ctx):
        return str(ctx.get("base_url", "https://api.openai.com/v1")).rstrip("/")

    async def list_voices(self, ctx, lang=None):
        return [VoiceInfo(id=v, name=v.title(), lang="multi") for v in OPENAI_VOICES]

    def default_voice(self, ctx):
        return ctx.get("default_voice", "nova")

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        body: dict[str, Any] = {"model": ctx.get("model", "gpt-4o-mini-tts"),
                                "voice": voice or self.default_voice(ctx), "input": text,
                                "response_format": "mp3"}
        if ctx.get("instructions") and body["model"] == "gpt-4o-mini-tts":
            body["instructions"] = ctx.get("instructions")
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", f"{self._base(ctx)}/audio/speech", provider=self.name,
                           headers={"Authorization": f"Bearer {_key(ctx, self.name)}"},
                           json=body)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(r.content)
        return TTSAudio(path=out_path, duration_s=media_duration(out_path), words=[])

    async def test_connection(self, ctx):
        async with client(ctx.timeout) as http:
            await send(http, "GET", f"{self._base(ctx)}/models", provider=self.name,
                       headers={"Authorization": f"Bearer {_key(ctx, self.name)}"})
        return TestResult(ok=True, message="Kết nối thành công")


# ----------------------------------------------------------------------------- Piper (offline)

PIPER_VOICES = {
    "vi_VN-vais1000-medium": "vi/vi_VN/vais1000/medium",
    "vi_VN-25hours_single-low": "vi/vi_VN/25hours_single/low",
    "vi_VN-vivos-x_low": "vi/vi_VN/vivos/x_low",
}
PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


class PiperTTS(TTSProvider):
    id: ClassVar[str] = "piper"
    name: ClassVar[str] = "Piper (offline)"
    description: ClassVar[str] = (
        "Đọc hoàn toàn trên máy, không cần internet sau khi tải giọng (~60 MB). Cần cài gói "
        "tuỳ chọn 'piper-tts' (bản cài đặt mặc định không kèm).")
    docs_url: ClassVar[str] = "https://github.com/OHF-Voice/piper1-gpl"
    requires_network: ClassVar[bool] = False
    audio_ext: ClassVar[str] = ".wav"

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "default_voice": {"type": "string", "title": "Giọng",
                                  "default": "vi_VN-vais1000-medium",
                                  "enum": list(PIPER_VOICES)},
                "length_scale": {"type": "number", "title": "Độ dài (1 = bình thường, lớn hơn "
                                 "= chậm hơn)", "default": 1.0, "minimum": 0.5, "maximum": 2.0},
            },
        }

    @staticmethod
    def available() -> bool:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return True

    async def list_voices(self, ctx, lang=None):
        return [VoiceInfo(id=v, name=v, lang="vi-VN") for v in PIPER_VOICES]

    def default_voice(self, ctx):
        return ctx.get("default_voice", "vi_VN-vais1000-medium")

    async def ensure_model(self, ctx: ProviderContext, voice: str) -> Path:
        if voice not in PIPER_VOICES:
            raise ProviderConfigError(f"{self.name}: giọng không hỗ trợ: {voice}",
                                      provider=self.id)
        root = (ctx.models_dir or Path(tempfile.gettempdir())) / "piper"
        model = root / f"{voice}.onnx"
        config = root / f"{voice}.onnx.json"
        if model.is_file() and config.is_file():
            return model
        root.mkdir(parents=True, exist_ok=True)
        base = f"{PIPER_BASE}/{PIPER_VOICES[voice]}/{voice}"
        async with client(600) as http:
            for url, dest in ((f"{base}.onnx.json", config), (f"{base}.onnx", model)):
                tmp = dest.with_suffix(dest.suffix + ".part")
                async with http.stream("GET", url) as resp:
                    if resp.status_code >= 400:
                        raise ProviderUnavailableError(
                            f"{self.name}: không tải được giọng ({resp.status_code})",
                            provider=self.id)
                    with open(tmp, "wb") as fh:
                        async for chunk in resp.aiter_bytes(1 << 16):
                            fh.write(chunk)
                tmp.replace(dest)
        return model

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        if not self.available():
            raise ProviderUnavailableError(
                f"{self.name}: chưa cài gói piper-tts (xem docs/providers.md)", provider=self.id)
        voice = voice or self.default_voice(ctx)
        model = await self.ensure_model(ctx, voice)
        length_scale = float(ctx.get("length_scale", 1.0))

        def run() -> None:
            import wave

            from piper import PiperVoice, SynthesisConfig

            pv = PiperVoice.load(str(model))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_path), "wb") as wf:
                pv.synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=length_scale))

        await asyncio.to_thread(run)
        return TTSAudio(path=out_path, duration_s=media_duration(out_path), words=[])

    async def test_connection(self, ctx):
        if not self.available():
            return TestResult(ok=False, message="Chưa cài gói piper-tts. Bản cài đặt mặc định "
                              "không kèm Piper; xem docs/providers.md.")
        return await _sample(self, ctx)


# ----------------------------------------------------------------------------- Mock


class MockTTS(TTSProvider):
    id: ClassVar[str] = "mock_tts"
    name: ClassVar[str] = "Giọng giả lập (tiếng bíp, offline)"
    description: ClassVar[str] = (
        "Không đọc thật: phát tiếng bíp theo từng từ với thời lượng ước tính. Dùng khi không "
        "có mạng hoặc để thử nhanh.")
    requires_network: ClassVar[bool] = False
    is_mock: ClassVar[bool] = True
    audio_ext: ClassVar[str] = ".wav"

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "default_voice": {"type": "string", "title": "Giọng", "default": "beep-low",
                                  "enum": ["beep-low", "beep-high", "silent"],
                                  "x-enumLabels": {"beep-low": "Bíp trầm",
                                                   "beep-high": "Bíp cao",
                                                   "silent": "Im lặng"}},
                "words_per_second": {"type": "number", "title": "Số từ / giây", "default": 3.0,
                                     "minimum": 0.5, "maximum": 10},
            },
        }

    async def list_voices(self, ctx, lang=None):
        return [VoiceInfo(id="beep-low", name="Bíp trầm", lang="any"),
                VoiceInfo(id="beep-high", name="Bíp cao", lang="any"),
                VoiceInfo(id="silent", name="Im lặng", lang="any")]

    def default_voice(self, ctx):
        return ctx.get("default_voice", "beep-low")

    async def synthesize(self, ctx, text, *, voice, lang, out_path):
        voice = voice or self.default_voice(ctx)
        wps = float(ctx.get("words_per_second", 3.0))
        n = max(1, len(tokenize(text)))
        duration = round(max(0.8, n / wps + 0.3), 2)
        words = proportional_timings(text, duration)
        await asyncio.to_thread(
            write_tone_wav, out_path, duration,
            freq=660.0 if voice == "beep-high" else 330.0,
            volume=0.0 if voice == "silent" else 0.12,
            word_spans=[(w.start, w.start + (w.end - w.start) * 0.6) for w in words])
        return TTSAudio(path=out_path, duration_s=duration, words=words)

    async def test_connection(self, ctx):
        return TestResult(ok=True, message="Giọng giả lập luôn sẵn sàng")


PROVIDERS = [EdgeTTS, AzureTTS, GoogleTTS, ElevenLabsTTS, FPTTTS, OpenAITTS, PiperTTS, MockTTS]
