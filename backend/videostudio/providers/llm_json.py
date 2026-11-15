"""Structured JSON output from any LLM provider: schema in the request, Pydantic
validation and one automatic repair retry."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .base import ChatMessage, LLMProvider, ProviderContext
from .errors import ProviderOutputError

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local ``$ref``s: several APIs (Gemini, some OpenAI-compatible servers)
    do not support ``$defs``."""
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                target = resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
                extra = {k: v for k, v in node.items() if k != "$ref"}
                return {**target, **extra}
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for match in _FENCE.finditer(text):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start >= 0:
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            return json.loads(text[start:end + 1])
    raise json.JSONDecodeError("no JSON object found", text, 0)


def _errors(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:10]
        )
    return str(exc)


async def complete_json(
    provider: LLMProvider,
    ctx: ProviderContext,
    messages: list[ChatMessage],
    model_cls: type[T],
    *,
    model: str | None = None,
    temperature: float = 0.5,
) -> T:
    schema = inline_refs(model_cls.model_json_schema())
    schema_text = json.dumps(schema, ensure_ascii=False)
    msgs = list(messages)
    if msgs and msgs[0].role == "system":
        msgs[0] = ChatMessage(
            role="system",
            content=msgs[0].content + "\n\nChỉ trả lời bằng JSON hợp lệ theo JSON Schema sau:\n"
            + schema_text,
        )
    response = await provider.complete(ctx, msgs, model=model, json_schema=schema,
                                       temperature=temperature)
    try:
        return model_cls.model_validate(extract_json(response.text))
    except (json.JSONDecodeError, ValidationError, ValueError) as first:
        repair = msgs + [
            ChatMessage(role="assistant", content=response.text[:8000]),
            ChatMessage(
                role="user",
                content=(
                    "Câu trả lời trước không hợp lệ: " + _errors(first)
                    + "\nHãy trả lời lại CHỈ bằng một đối tượng JSON hợp lệ đúng schema, "
                    "không kèm giải thích."
                ),
            ),
        ]
        response = await provider.complete(ctx, repair, model=model, json_schema=schema,
                                           temperature=0.2)
        try:
            return model_cls.model_validate(extract_json(response.text))
        except (json.JSONDecodeError, ValidationError, ValueError) as second:
            raise ProviderOutputError(
                f"{provider.name}: kết quả JSON không hợp lệ sau khi thử sửa: {_errors(second)}",
                provider=provider.id,
            ) from second
