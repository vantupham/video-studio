"""LLM providers (plain httpx, no vendor SDKs)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, ClassVar

from ..base import (
    ChatMessage,
    LLMProvider,
    LLMResponse,
    ModelInfo,
    ProviderContext,
    TestResult,
    secret_field,
    timeout_field,
)
from ..errors import ProviderConfigError, ProviderOutputError, client, send


def _require(ctx: ProviderContext, key: str, label: str, provider: str) -> str:
    value = ctx.get(key)
    if not value:
        raise ProviderConfigError(f"{provider}: chưa cấu hình {label}", provider=provider)
    return str(value)


def _model(ctx: ProviderContext, model: str | None, provider: str) -> str:
    return model or _require(ctx, "model", "model", provider)


def _temperature(ctx: ProviderContext, value: float) -> float:
    configured = ctx.get("temperature")
    return float(configured) if configured is not None else value


# ----------------------------------------------------------------------------- OpenAI


class OpenAILLM(LLMProvider):
    id: ClassVar[str] = "openai"
    name: ClassVar[str] = "OpenAI"
    description: ClassVar[str] = "GPT qua API chính thức của OpenAI (trả phí theo token)."
    docs_url: ClassVar[str] = "https://platform.openai.com/api-keys"
    default_base_url: ClassVar[str] = "https://api.openai.com/v1"
    default_model: ClassVar[str] = "gpt-4o-mini"

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["api_key", "model"],
            "properties": {
                "api_key": secret_field("API key", "Tạo tại platform.openai.com/api-keys"),
                "model": {"type": "string", "title": "Model", "default": self.default_model,
                          "x-source": "models"},
                "base_url": {"type": "string", "title": "Base URL",
                             "default": self.default_base_url},
                "organization": {"type": "string", "title": "Organization ID (tuỳ chọn)"},
                "temperature": {"type": "number", "title": "Temperature", "minimum": 0,
                                "maximum": 2},
                "timeout_s": timeout_field(120),
            },
        }

    def _base(self, ctx: ProviderContext) -> str:
        return str(ctx.get("base_url", self.default_base_url)).rstrip("/")

    def _headers(self, ctx: ProviderContext) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = ctx.get("api_key")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if ctx.get("organization"):
            headers["OpenAI-Organization"] = str(ctx.get("organization"))
        return headers

    def _check(self, ctx: ProviderContext) -> None:
        _require(ctx, "api_key", "API key", self.name)

    def _filter_model(self, model_id: str) -> bool:
        return bool(re.match(r"^(gpt|o\d|chatgpt)", model_id)) and not any(
            s in model_id for s in ("audio", "realtime", "transcribe", "tts", "image", "search")
        )

    async def list_models(self, ctx: ProviderContext) -> list[ModelInfo]:
        self._check(ctx)
        async with client(ctx.timeout) as http:
            r = await send(http, "GET", f"{self._base(ctx)}/models", provider=self.name,
                           headers=self._headers(ctx))
        ids = sorted(m["id"] for m in r.json().get("data", []) if self._filter_model(m["id"]))
        return [ModelInfo(id=i) for i in ids]

    async def test_connection(self, ctx: ProviderContext) -> TestResult:
        models = await self.list_models(ctx)
        return TestResult(ok=True, message=f"Kết nối thành công ({len(models)} model khả dụng)",
                          details={"models": len(models)})

    def _json_format(self, ctx: ProviderContext, schema: dict[str, Any]) -> dict[str, Any] | None:
        return {"type": "json_schema",
                "json_schema": {"name": "output", "schema": schema, "strict": False}}

    async def complete(self, ctx, messages, *, model=None, json_schema=None, temperature=0.7,
                       max_tokens=None) -> LLMResponse:
        self._check(ctx)
        model_id = _model(ctx, model, self.name)
        body: dict[str, Any] = {"model": model_id,
                                "messages": [m.model_dump() for m in messages]}
        if not re.match(r"^(o\d|gpt-5)", model_id):
            body["temperature"] = _temperature(ctx, temperature)
        if max_tokens:
            body["max_tokens"] = max_tokens
        if json_schema is not None:
            fmt = self._json_format(ctx, json_schema)
            if fmt:
                body["response_format"] = fmt
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", f"{self._base(ctx)}/chat/completions",
                           provider=self.name, headers=self._headers(ctx), json=body)
        data = r.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderOutputError(f"{self.name}: phản hồi không đúng định dạng",
                                      provider=self.id) from exc
        return LLMResponse(text=text, model=data.get("model", model_id),
                           usage=data.get("usage") or {})


class OpenAICompatibleLLM(OpenAILLM):
    id: ClassVar[str] = "openai_compatible"
    name: ClassVar[str] = "OpenAI-compatible (tuỳ chỉnh)"
    description: ClassVar[str] = (
        "Bất kỳ máy chủ nào có API /v1/chat/completions: OpenRouter, Groq, DeepSeek, "
        "LM Studio, vLLM, LocalAI…")
    docs_url: ClassVar[str | None] = None

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["base_url", "model"],
            "properties": {
                "base_url": {"type": "string", "title": "Base URL",
                             "description": "Ví dụ: https://openrouter.ai/api/v1 hoặc "
                                            "http://localhost:1234/v1"},
                "api_key": secret_field("API key (nếu máy chủ yêu cầu)"),
                "model": {"type": "string", "title": "Model", "x-source": "models"},
                "json_mode": {
                    "type": "string", "title": "Chế độ JSON",
                    "enum": ["json_schema", "json_object", "none"], "default": "json_object",
                    "x-enumLabels": {"json_schema": "JSON Schema (structured outputs)",
                                     "json_object": "JSON object",
                                     "none": "Không (chỉ dựa vào prompt)"},
                },
                "temperature": {"type": "number", "title": "Temperature", "minimum": 0,
                                "maximum": 2},
                "timeout_s": timeout_field(120),
            },
        }

    def _base(self, ctx: ProviderContext) -> str:
        return _require(ctx, "base_url", "Base URL", self.name).rstrip("/")

    def _check(self, ctx: ProviderContext) -> None:
        self._base(ctx)

    def _filter_model(self, model_id: str) -> bool:
        return True

    def _json_format(self, ctx, schema):
        mode = ctx.get("json_mode", "json_object")
        if mode == "json_schema":
            return super()._json_format(ctx, schema)
        if mode == "json_object":
            return {"type": "json_object"}
        return None


# ----------------------------------------------------------------------------- Anthropic


class AnthropicLLM(LLMProvider):
    id: ClassVar[str] = "anthropic"
    name: ClassVar[str] = "Anthropic Claude"
    description: ClassVar[str] = "Claude qua Messages API (trả phí theo token)."
    docs_url: ClassVar[str] = "https://console.anthropic.com/settings/keys"

    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["api_key", "model"],
            "properties": {
                "api_key": secret_field("API key", "Tạo tại console.anthropic.com"),
                "model": {"type": "string", "title": "Model", "default": "claude-sonnet-4-5",
                          "x-source": "models"},
                "base_url": {"type": "string", "title": "Base URL",
                             "default": "https://api.anthropic.com"},
                "max_tokens": {"type": "integer", "title": "Số token tối đa", "default": 8192},
                "temperature": {"type": "number", "title": "Temperature", "minimum": 0,
                                "maximum": 1},
                "timeout_s": timeout_field(180),
            },
        }

    def _base(self, ctx):
        return str(ctx.get("base_url", "https://api.anthropic.com")).rstrip("/")

    def _headers(self, ctx):
        return {"x-api-key": _require(ctx, "api_key", "API key", self.name),
                "anthropic-version": "2023-06-01", "content-type": "application/json"}

    async def list_models(self, ctx):
        async with client(ctx.timeout) as http:
            r = await send(http, "GET", f"{self._base(ctx)}/v1/models?limit=100",
                           provider=self.name, headers=self._headers(ctx))
        return [ModelInfo(id=m["id"], name=m.get("display_name"))
                for m in r.json().get("data", [])]

    async def test_connection(self, ctx):
        models = await self.list_models(ctx)
        return TestResult(ok=True, message=f"Kết nối thành công ({len(models)} model khả dụng)")

    async def complete(self, ctx, messages, *, model=None, json_schema=None, temperature=0.7,
                       max_tokens=None):
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        chat = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        body: dict[str, Any] = {
            "model": _model(ctx, model, self.name),
            "max_tokens": int(max_tokens or ctx.get("max_tokens", 8192)),
            "messages": chat,
            "temperature": min(_temperature(ctx, temperature), 1.0),
        }
        if system:
            body["system"] = system
        if json_schema is not None:
            body["tools"] = [{"name": "output", "description": "Trả kết quả có cấu trúc",
                              "input_schema": json_schema}]
            body["tool_choice"] = {"type": "tool", "name": "output"}
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", f"{self._base(ctx)}/v1/messages", provider=self.name,
                           headers=self._headers(ctx), json=body)
        data = r.json()
        blocks = data.get("content") or []
        for block in blocks:
            if block.get("type") == "tool_use":
                text = json.dumps(block.get("input", {}), ensure_ascii=False)
                break
        else:
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        return LLMResponse(text=text, model=data.get("model"), usage=data.get("usage") or {})


# ----------------------------------------------------------------------------- Gemini


class GeminiLLM(LLMProvider):
    id: ClassVar[str] = "gemini"
    name: ClassVar[str] = "Google Gemini"
    description: ClassVar[str] = "Gemini qua Google AI Studio API (có hạn mức miễn phí)."
    docs_url: ClassVar[str] = "https://aistudio.google.com/app/apikey"

    def config_schema(self):
        return {
            "type": "object",
            "required": ["api_key", "model"],
            "properties": {
                "api_key": secret_field("API key", "Tạo tại aistudio.google.com"),
                "model": {"type": "string", "title": "Model", "default": "gemini-2.5-flash",
                          "x-source": "models"},
                "base_url": {"type": "string", "title": "Base URL",
                             "default": "https://generativelanguage.googleapis.com/v1beta"},
                "temperature": {"type": "number", "title": "Temperature", "minimum": 0,
                                "maximum": 2},
                "timeout_s": timeout_field(120),
            },
        }

    def _base(self, ctx):
        return str(ctx.get("base_url",
                           "https://generativelanguage.googleapis.com/v1beta")).rstrip("/")

    def _headers(self, ctx):
        return {"x-goog-api-key": _require(ctx, "api_key", "API key", self.name),
                "Content-Type": "application/json"}

    async def list_models(self, ctx):
        async with client(ctx.timeout) as http:
            r = await send(http, "GET", f"{self._base(ctx)}/models?pageSize=200",
                           provider=self.name, headers=self._headers(ctx))
        out = []
        for m in r.json().get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                out.append(ModelInfo(id=m["name"].removeprefix("models/"),
                                     name=m.get("displayName")))
        return out

    async def test_connection(self, ctx):
        models = await self.list_models(ctx)
        return TestResult(ok=True, message=f"Kết nối thành công ({len(models)} model khả dụng)")

    async def complete(self, ctx, messages, *, model=None, json_schema=None, temperature=0.7,
                       max_tokens=None):
        model_id = _model(ctx, model, self.name).removeprefix("models/")
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [{"role": "model" if m.role == "assistant" else "user",
                     "parts": [{"text": m.content}]} for m in messages if m.role != "system"]
        gen: dict[str, Any] = {"temperature": _temperature(ctx, temperature)}
        if max_tokens:
            gen["maxOutputTokens"] = max_tokens
        if json_schema is not None:
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", f"{self._base(ctx)}/models/{model_id}:generateContent",
                           provider=self.name, headers=self._headers(ctx), json=body)
        data = r.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as exc:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "không có nội dung")
            raise ProviderOutputError(f"{self.name}: phản hồi rỗng ({reason})",
                                      provider=self.id) from exc
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return LLMResponse(text=text, model=model_id, usage=data.get("usageMetadata") or {})


# ----------------------------------------------------------------------------- Ollama


class OllamaLLM(LLMProvider):
    id: ClassVar[str] = "ollama"
    name: ClassVar[str] = "Ollama (chạy trên máy)"
    description: ClassVar[str] = "Mô hình chạy cục bộ qua Ollama — miễn phí, không cần internet."
    docs_url: ClassVar[str] = "https://ollama.com/download"
    requires_network: ClassVar[bool] = False

    def config_schema(self):
        return {
            "type": "object",
            "required": ["base_url", "model"],
            "properties": {
                "base_url": {"type": "string", "title": "Địa chỉ Ollama",
                             "default": "http://localhost:11434"},
                "model": {"type": "string", "title": "Model", "default": "qwen2.5:7b",
                          "x-source": "models"},
                "temperature": {"type": "number", "title": "Temperature", "minimum": 0,
                                "maximum": 2},
                "timeout_s": timeout_field(300),
            },
        }

    def _base(self, ctx):
        return str(ctx.get("base_url", "http://localhost:11434")).rstrip("/")

    async def list_models(self, ctx):
        async with client(ctx.timeout) as http:
            r = await send(http, "GET", f"{self._base(ctx)}/api/tags", provider=self.name)
        return [ModelInfo(id=m["name"]) for m in r.json().get("models", [])]

    async def test_connection(self, ctx):
        models = await self.list_models(ctx)
        if not models:
            return TestResult(ok=False, message="Ollama đang chạy nhưng chưa có model nào. "
                                                "Chạy: ollama pull qwen2.5:7b")
        return TestResult(ok=True, message=f"Kết nối thành công ({len(models)} model)")

    async def complete(self, ctx, messages, *, model=None, json_schema=None, temperature=0.7,
                       max_tokens=None):
        body: dict[str, Any] = {
            "model": _model(ctx, model, self.name),
            "messages": [m.model_dump() for m in messages],
            "stream": False,
            "options": {"temperature": _temperature(ctx, temperature)},
        }
        if max_tokens:
            body["options"]["num_predict"] = max_tokens
        if json_schema is not None:
            body["format"] = json_schema
        async with client(ctx.timeout) as http:
            r = await send(http, "POST", f"{self._base(ctx)}/api/chat", provider=self.name,
                           json=body)
        data = r.json()
        return LLMResponse(text=(data.get("message") or {}).get("content", ""),
                           model=data.get("model"),
                           usage={"prompt_eval_count": data.get("prompt_eval_count"),
                                  "eval_count": data.get("eval_count")})


# ----------------------------------------------------------------------------- Mock


class MockLLM(LLMProvider):
    """Deterministic offline LLM for tests and demo mode. It reads the ```json payload
    block that the built-in prompt templates embed and builds a plausible answer."""

    id: ClassVar[str] = "mock_llm"
    name: ClassVar[str] = "Chế độ demo (giả lập, không cần key)"
    description: ClassVar[str] = (
        "Tạo kịch bản từ chính nội dung tài liệu mà không gọi AI. Dùng để thử ứng dụng.")
    requires_network: ClassVar[bool] = False
    is_mock: ClassVar[bool] = True

    def config_schema(self):
        return {
            "type": "object",
            "properties": {
                "delay_ms": {"type": "integer", "title": "Độ trễ giả lập (ms)", "default": 0,
                             "minimum": 0, "maximum": 10000},
                "fail_first_json": {"type": "boolean", "title": "Trả JSON lỗi ở lần đầu "
                                    "(thử cơ chế tự sửa)", "default": False},
            },
        }

    async def list_models(self, ctx):
        return [ModelInfo(id="mock-1", name="Mock 1")]

    async def test_connection(self, ctx):
        return TestResult(ok=True, message="Chế độ demo luôn sẵn sàng")

    async def complete(self, ctx, messages, *, model=None, json_schema=None, temperature=0.7,
                       max_tokens=None):
        delay = int(ctx.get("delay_ms", 0) or 0)
        if delay:
            await asyncio.sleep(delay / 1000)
        repairing = any(m.role == "assistant" for m in messages)
        if json_schema is not None and ctx.get("fail_first_json") and not repairing:
            return LLMResponse(text="Đây không phải JSON {", model="mock-1")
        payload = _payload(messages)
        if json_schema is None:
            return LLMResponse(text="Xin chào từ chế độ demo.", model="mock-1")
        task = payload.get("task")
        if task == "scene":
            result = mock_scene(payload)
        else:
            result = mock_script(payload)
        return LLMResponse(text=json.dumps(result, ensure_ascii=False), model="mock-1")


_PAYLOAD = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _payload(messages: list[ChatMessage]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.role != "user":
            continue
        for match in reversed(_PAYLOAD.findall(message.content)):
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue
    return {}


_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


def _trim_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    cut = " ".join(words[:max_words]).rstrip(",;:")
    return cut + ("" if cut.endswith((".", "!", "?")) else ".")


def mock_script(payload: dict[str, Any]) -> dict[str, Any]:
    title = payload.get("title") or "Video"
    sections = [s for s in payload.get("sections", []) if (s.get("text") or s.get("bullets"))]
    target = float(payload.get("target_duration_s") or 30)
    wps = float(payload.get("words_per_second") or 2.6)
    max_scenes = int(payload.get("max_scenes") or 8)
    sections = sections[: max(1, max_scenes - 1)]
    budget = max(8, int(target * wps))
    intro_words = max(5, min(18, budget // (len(sections) + 1) if sections else budget))
    per_section = max(6, (budget - intro_words) // max(1, len(sections)))
    scenes = [{
        "narration": _trim_words(f"{title}. {payload.get('summary') or ''}".strip(),
                                 intro_words),
        "visual_type": "title_card",
        "title": title,
        "bullets": [],
        "image_query": None,
    }]
    for section in sections:
        text = " ".join(_SENTENCE.split((section.get("text") or "").strip())[:3])
        bullets = [b for b in section.get("bullets", []) if b][:4]
        narration = text or ". ".join(bullets)
        scenes.append({
            "narration": _trim_words(narration, per_section),
            "visual_type": "text_card",
            "title": section.get("heading") or title,
            "bullets": bullets or [_trim_words(s, 10) for s in _SENTENCE.split(text)[:3] if s],
            "image_query": section.get("heading"),
        })
    return {"title": title, "scenes": scenes}


def mock_scene(payload: dict[str, Any]) -> dict[str, Any]:
    scene = dict(payload.get("scene") or {})
    instruction = (payload.get("instruction") or "").strip()
    narration = scene.get("narration", "")
    lowered = instruction.lower()
    if "ngắn" in lowered or "short" in lowered:
        narration = _SENTENCE.split(narration)[0]
    elif instruction:
        narration = f"{narration.rstrip('.')} — {instruction}."
    return {
        "narration": narration,
        "visual_type": scene.get("visual_type") or "text_card",
        "title": scene.get("title"),
        "bullets": scene.get("bullets") or [],
        "image_query": scene.get("image_query"),
    }


PROVIDERS = [OpenAILLM, OpenAICompatibleLLM, AnthropicLLM, GeminiLLM, OllamaLLM, MockLLM]
