"""Provider errors and HTTP helpers. Messages are user-facing (Vietnamese) and never
contain secrets."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx


class ProviderError(Exception):
    code = "error"

    def __init__(self, message: str, *, status: int | None = None, provider: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.provider = provider

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "status": self.status,
                "provider": self.provider}


class ProviderAuthError(ProviderError):
    code = "auth"


class ProviderQuotaError(ProviderError):
    code = "quota"


class ProviderTimeoutError(ProviderError):
    code = "timeout"


class ProviderUnavailableError(ProviderError):
    code = "unavailable"


class ProviderConfigError(ProviderError):
    code = "config"


class ProviderHTTPError(ProviderError):
    code = "http"


class ProviderOutputError(ProviderError):
    code = "output"


_QUOTA_HINTS = ("insufficient_quota", "quota", "rate limit", "rate_limit", "credits",
                "resource_exhausted", "quota_exceeded")


# Upstream error bodies sometimes echo the (partially masked) key, e.g. OpenAI's
# "Incorrect API key provided: sk-abc*****wxyz". Anything key-shaped is replaced.
_KEYLIKE = re.compile(r"\b(?:sk|pk|rk|xai|AIza|key)[-_A-Za-z0-9*.]{6,}|[A-Za-z0-9_\-*]{24,}")


def redact(text: str) -> str:
    return _KEYLIKE.sub("••••", text)


def _excerpt(response: httpx.Response, limit: int = 300) -> str:
    return redact(_raw_excerpt(response, limit))


def _raw_excerpt(response: httpx.Response, limit: int) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            err = data.get("error", data)
            if isinstance(err, dict):
                msg = err.get("message") or err.get("detail") or json.dumps(err)[:limit]
                return str(msg)[:limit]
            if isinstance(data.get("detail"), dict):
                return str(data["detail"].get("message", data["detail"]))[:limit]
            return str(err)[:limit]
    except ValueError:
        pass
    return response.text[:limit]


def raise_for_response(response: httpx.Response, provider: str) -> None:
    if response.status_code < 400:
        return
    excerpt = _excerpt(response)
    status = response.status_code
    lowered = excerpt.lower()
    if status in (401, 403):
        raise ProviderAuthError(
            f"{provider}: API key không hợp lệ hoặc không có quyền ({status}). {excerpt}",
            status=status, provider=provider)
    if status in (402, 429) or any(h in lowered for h in _QUOTA_HINTS):
        raise ProviderQuotaError(
            f"{provider}: hết hạn mức / vượt giới hạn gọi API ({status}). {excerpt}",
            status=status, provider=provider)
    raise ProviderHTTPError(f"{provider}: lỗi HTTP {status}. {excerpt}", status=status,
                            provider=provider)


async def send(
    client: httpx.AsyncClient, method: str, url: str, *, provider: str, **kwargs: Any
) -> httpx.Response:
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ProviderTimeoutError(f"{provider}: quá thời gian chờ phản hồi", provider=provider) \
            from exc
    except httpx.TransportError as exc:
        raise ProviderUnavailableError(
            f"{provider}: không kết nối được máy chủ ({type(exc).__name__})", provider=provider
        ) from exc
    raise_for_response(response, provider)
    return response


def client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 15)),
                             follow_redirects=True)
