"""Provider plugins (LLM, TTS, alignment, media)."""

from __future__ import annotations

from .base import (
    CAPABILITIES,
    CAPABILITY_LABELS,
    AlignmentProvider,
    BaseProvider,
    ChatMessage,
    LLMProvider,
    LLMResponse,
    MediaItem,
    MediaProvider,
    ModelInfo,
    OCRLine,
    OCRProvider,
    OCRResult,
    ProviderContext,
    TestResult,
    TTSAudio,
    TTSProvider,
    VoiceInfo,
    WordTiming,
    secret_field,
)


def builtin_providers() -> list[BaseProvider]:
    from . import alignment, llm, media, ocr, tts

    return [cls() for mod in (llm, tts, alignment, media, ocr) for cls in mod.PROVIDERS]


__all__ = [
    "CAPABILITIES",
    "CAPABILITY_LABELS",
    "AlignmentProvider",
    "BaseProvider",
    "ChatMessage",
    "LLMProvider",
    "LLMResponse",
    "MediaItem",
    "MediaProvider",
    "ModelInfo",
    "OCRLine",
    "OCRProvider",
    "OCRResult",
    "ProviderContext",
    "TTSAudio",
    "TTSProvider",
    "TestResult",
    "VoiceInfo",
    "WordTiming",
    "builtin_providers",
    "secret_field",
]
