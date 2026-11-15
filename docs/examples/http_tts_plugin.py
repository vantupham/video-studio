"""Ví dụ plugin: nhà cung cấp giọng đọc gọi một máy chủ TTS HTTP của riêng bạn.

Cách dùng: chép file này vào thư mục ``plugins/`` trong thư mục dữ liệu
(Windows: ``%LOCALAPPDATA%\\VideoStudio\\plugins\\http_tts_plugin.py``) rồi khởi động lại
ứng dụng. Trong Cài đặt → Nhà cung cấp → Giọng đọc → "+ Thêm" sẽ có mục
"Máy chủ TTS riêng (ví dụ)".

Máy chủ cần có:
  GET  {base_url}/voices            → [{"id": "...", "name": "...", "lang": "vi"}]
  POST {base_url}/tts {text, voice} → audio/mpeg (byte MP3)
"""

from __future__ import annotations

from pathlib import Path

from videostudio.media.audio import media_duration
from videostudio.plugins.base import TTSProvider
from videostudio.providers.base import (
    ProviderContext,
    TestResult,
    TTSAudio,
    VoiceInfo,
    secret_field,
    timeout_field,
)
from videostudio.providers.errors import ProviderConfigError, client, send


class HttpTTS(TTSProvider):
    id = "example_http_tts"
    name = "Máy chủ TTS riêng (ví dụ)"
    description = "Gọi máy chủ TTS HTTP tự dựng (plugin mẫu trong docs/examples)."
    docs_url = None
    audio_ext = ".mp3"

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "required": ["base_url"],
            "properties": {
                "base_url": {"type": "string", "title": "Địa chỉ máy chủ",
                             "default": "http://127.0.0.1:5002"},
                # Trường x-secret được lưu trong SecretStore (DPAPI), không vào SQLite.
                "api_key": secret_field("Token (nếu máy chủ yêu cầu)"),
                "default_voice": {"type": "string", "title": "Giọng mặc định",
                                  "x-source": "voices"},
                "timeout_s": timeout_field(60),
            },
        }

    def _headers(self, ctx: ProviderContext) -> dict[str, str]:
        key = ctx.get("api_key")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _base(self, ctx: ProviderContext) -> str:
        base = (ctx.get("base_url") or "").rstrip("/")
        if not base:
            raise ProviderConfigError(f"{self.name}: chưa nhập địa chỉ máy chủ", provider=self.name)
        return base

    async def list_voices(self, ctx: ProviderContext, lang: str | None = None) -> list[VoiceInfo]:
        async with client(ctx.timeout) as http:
            res = await send(http, "GET", f"{self._base(ctx)}/voices", provider=self.name,
                             headers=self._headers(ctx))
        voices = [VoiceInfo(**v) for v in res.json()]
        return [v for v in voices if not lang or v.lang.startswith(lang)]

    async def synthesize(self, ctx: ProviderContext, text: str, *, voice: str | None,
                         lang: str, out_path: Path) -> TTSAudio:
        async with client(ctx.timeout) as http:
            res = await send(http, "POST", f"{self._base(ctx)}/tts", provider=self.name,
                             headers=self._headers(ctx),
                             json={"text": text, "voice": voice or self.default_voice(ctx),
                                   "lang": lang})
        out_path.write_bytes(res.content)
        # words=[]: không có mốc thời gian từng từ → phụ đề được căn bằng nhà cung cấp
        # "Căn thời gian phụ đề" mặc định (ước lượng theo âm tiết hoặc faster-whisper).
        return TTSAudio(path=out_path, duration_s=media_duration(out_path), words=[])

    async def test_connection(self, ctx: ProviderContext) -> TestResult:
        voices = await self.list_voices(ctx)
        return TestResult(ok=True, message=f"Kết nối được, có {len(voices)} giọng")


def register(registry) -> None:
    registry.add_provider(HttpTTS())
