"""Provider plugin interfaces.

A *provider type* (e.g. ``openai``, ``azure_tts``) is a stateless class registered in the
plugin registry. A *provider instance* is a user-configured copy stored in the database
(non-secret config) + SecretStore (secret fields). At call time the service resolves an
instance into a :class:`ProviderContext` holding the full config, secrets included.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

Capability = Literal["llm", "tts", "alignment", "media"]
CAPABILITIES: tuple[str, ...] = ("llm", "tts", "alignment", "media")
CAPABILITY_LABELS = {
    "llm": "Mô hình ngôn ngữ (LLM)",
    "tts": "Chuyển văn bản thành giọng nói (TTS)",
    "alignment": "Căn thời gian phụ đề",
    "media": "Ảnh / video minh hoạ",
}


@dataclass
class ProviderContext:
    config: dict[str, Any]
    instance_id: str | None = None
    models_dir: Path | None = None
    cache_dir: Path | None = None

    def get(self, key: str, default: Any = None) -> Any:
        value = self.config.get(key)
        return default if value in (None, "") else value

    @property
    def timeout(self) -> float:
        return float(self.get("timeout_s", 60))


class TestResult(BaseModel):
    ok: bool
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    id: str
    name: str | None = None


class VoiceInfo(BaseModel):
    id: str
    name: str
    lang: str = ""
    gender: str | None = None
    description: str | None = None


class WordTiming(BaseModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class TTSAudio(BaseModel):
    path: Path
    duration_s: float
    words: list[WordTiming] = Field(default_factory=list)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMResponse(BaseModel):
    text: str
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class MediaItem(BaseModel):
    id: str
    kind: Literal["image", "video"] = "image"
    url: str
    preview_url: str | None = None
    width: int | None = None
    height: int | None = None
    author: str | None = None
    source: str | None = None


class BaseProvider(ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    capability: ClassVar[Capability]
    description: ClassVar[str] = ""
    docs_url: ClassVar[str | None] = None
    unofficial: ClassVar[bool] = False
    requires_network: ClassVar[bool] = True
    is_mock: ClassVar[bool] = False

    @abstractmethod
    def config_schema(self) -> dict[str, Any]: ...

    @abstractmethod
    async def test_connection(self, ctx: ProviderContext) -> TestResult: ...

    def secret_fields(self) -> list[str]:
        return [k for k, v in self.config_schema().get("properties", {}).items()
                if v.get("x-secret")]

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "capability": self.capability,
            "description": self.description,
            "docs_url": self.docs_url,
            "unofficial": self.unofficial,
            "requires_network": self.requires_network,
            "is_mock": self.is_mock,
            "config_schema": self.config_schema(),
            "secret_fields": self.secret_fields(),
        }


class LLMProvider(BaseProvider):
    capability: ClassVar[Capability] = "llm"

    async def list_models(self, ctx: ProviderContext) -> list[ModelInfo]:
        model = ctx.get("model")
        return [ModelInfo(id=model)] if model else []

    @abstractmethod
    async def complete(
        self,
        ctx: ProviderContext,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


class TTSProvider(BaseProvider):
    capability: ClassVar[Capability] = "tts"
    audio_ext: ClassVar[str] = ".mp3"

    @abstractmethod
    async def list_voices(self, ctx: ProviderContext, lang: str | None = None) -> list[VoiceInfo]:
        ...

    @abstractmethod
    async def synthesize(
        self, ctx: ProviderContext, text: str, *, voice: str | None, lang: str, out_path: Path
    ) -> TTSAudio:
        """Write audio to ``out_path`` (extension given by :attr:`audio_ext`)."""

    def default_voice(self, ctx: ProviderContext) -> str | None:
        return ctx.get("default_voice")

    def cache_settings(self, ctx: ProviderContext) -> dict[str, Any]:
        """Config values that change the produced audio (part of the TTS cache key)."""
        secrets = set(self.secret_fields())
        skip = secrets | {"timeout_s", "default_voice", "base_url", "region", "endpoint"}
        return {k: v for k, v in sorted(ctx.config.items()) if k not in skip}


class AlignmentProvider(BaseProvider):
    capability: ClassVar[Capability] = "alignment"

    @abstractmethod
    async def align(
        self, ctx: ProviderContext, *, audio_path: Path, text: str, lang: str, duration_s: float
    ) -> list[WordTiming]: ...


class MediaProvider(BaseProvider):
    capability: ClassVar[Capability] = "media"

    @abstractmethod
    async def search(
        self, ctx: ProviderContext, query: str, *, kind: str = "image",
        orientation: str = "landscape", limit: int = 10,
    ) -> list[MediaItem]: ...

    @abstractmethod
    async def download(self, ctx: ProviderContext, item: MediaItem, dest: Path) -> Path: ...


@dataclass
class ResolvedProvider:
    provider: BaseProvider
    ctx: ProviderContext
    instance_id: str | None
    instance_name: str
    model: str | None = None
    voice: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------------- schema helpers


def secret_field(title: str, description: str = "", required_hint: bool = True) -> dict:
    return {
        "type": "string",
        "title": title,
        "description": description,
        "format": "password",
        "x-secret": True,
    }


def timeout_field(default: int = 60) -> dict:
    return {"type": "integer", "title": "Thời gian chờ (giây)", "default": default,
            "minimum": 5, "maximum": 600}
