"""Alignment providers: produce word timings for a narration audio file.

The pipeline prefers word timestamps returned by the TTS provider itself and only calls the
configured alignment provider when the TTS did not return any.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from ..base import AlignmentProvider, ProviderContext, TestResult, WordTiming
from ..errors import ProviderUnavailableError
from .text import proportional_timings, snap_to_text


class ProportionalAlignment(AlignmentProvider):
    id: ClassVar[str] = "proportional"
    name: ClassVar[str] = "Ước lượng theo âm tiết (offline)"
    description: ClassVar[str] = (
        "Chia thời lượng câu theo số âm tiết/ký tự và dấu câu. Luôn hoạt động, độ chính xác "
        "vừa phải.")
    requires_network: ClassVar[bool] = False

    def config_schema(self):
        return {"type": "object", "properties": {}}

    async def align(self, ctx, *, audio_path, text, lang, duration_s):
        return proportional_timings(text, duration_s)

    async def test_connection(self, ctx):
        return TestResult(ok=True, message="Luôn sẵn sàng")


class WhisperAlignment(AlignmentProvider):
    id: ClassVar[str] = "faster_whisper"
    name: ClassVar[str] = "faster-whisper (nhận dạng giọng nói, offline)"
    description: ClassVar[str] = (
        "Nghe lại audio để lấy mốc thời gian chính xác cho giọng không có timestamps (Azure, "
        "FPT, OpenAI, Piper). Model tải về khi dùng lần đầu vào thư mục models/. Cần gói tuỳ "
        "chọn 'faster-whisper' (không kèm trong bản cài đặt mặc định).")
    docs_url: ClassVar[str] = "https://github.com/SYSTRAN/faster-whisper"
    requires_network: ClassVar[bool] = False
    _models: ClassVar[dict[tuple[str, str, str], object]] = {}

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "model_size": {"type": "string", "title": "Kích thước model", "default": "small",
                               "enum": ["tiny", "base", "small", "medium", "large-v3"],
                               "x-enumLabels": {"tiny": "tiny (~75 MB)", "base": "base (~150 MB)",
                                                "small": "small (~500 MB)",
                                                "medium": "medium (~1.5 GB)",
                                                "large-v3": "large-v3 (~3 GB)"}},
                "device": {"type": "string", "title": "Thiết bị", "default": "auto",
                           "enum": ["auto", "cpu", "cuda"]},
                "compute_type": {"type": "string", "title": "Kiểu tính toán", "default": "int8",
                                 "enum": ["int8", "float16", "float32"]},
            },
        }

    @staticmethod
    def available() -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _model(self, ctx: ProviderContext):
        from faster_whisper import WhisperModel

        key = (str(ctx.get("model_size", "small")), str(ctx.get("device", "auto")),
               str(ctx.get("compute_type", "int8")))
        if key not in self._models:
            root = (ctx.models_dir or Path.cwd()) / "whisper"
            root.mkdir(parents=True, exist_ok=True)
            self._models[key] = WhisperModel(key[0], device=key[1], compute_type=key[2],
                                             download_root=str(root))
        return self._models[key]

    async def align(self, ctx, *, audio_path, text, lang, duration_s):
        if not self.available():
            raise ProviderUnavailableError(
                f"{self.name}: chưa cài gói faster-whisper (xem docs/providers.md)",
                provider=self.id)

        def run() -> list[WordTiming]:
            segments, _ = self._model(ctx).transcribe(
                str(audio_path), language=lang.split("-")[0], word_timestamps=True,
                initial_prompt=text[:200])
            return [WordTiming(text=w.word.strip(), start=max(0.0, w.start), end=max(0.0, w.end))
                    for seg in segments for w in (seg.words or []) if w.word.strip()]

        words = await asyncio.to_thread(run)
        return snap_to_text(words, text, duration_s)

    async def test_connection(self, ctx):
        if not self.available():
            return TestResult(ok=False, message="Chưa cài gói faster-whisper. Xem "
                                                "docs/providers.md để bật tính năng này.")
        return TestResult(ok=True, message="faster-whisper đã được cài")


class MockAlignment(ProportionalAlignment):
    id: ClassVar[str] = "mock_alignment"
    name: ClassVar[str] = "Căn thời gian giả lập (chia đều)"
    description: ClassVar[str] = "Chia đều thời lượng cho các từ. Dùng cho kiểm thử."
    is_mock: ClassVar[bool] = True

    async def align(self, ctx, *, audio_path, text, lang, duration_s):
        tokens = text.split()
        if not tokens:
            return []
        step = duration_s / len(tokens)
        return [WordTiming(text=t, start=round(i * step, 3), end=round((i + 1) * step, 3))
                for i, t in enumerate(tokens)]


PROVIDERS = [ProportionalAlignment, WhisperAlignment, MockAlignment]
