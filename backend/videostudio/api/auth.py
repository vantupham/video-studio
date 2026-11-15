"""Per-launch token authentication.

HTTP requests send ``Authorization: Bearer <token>`` (or ``X-VideoStudio-Token``).
Browsers cannot set headers on WebSocket handshakes, so ``/ws/*`` accepts
``?token=<token>`` instead.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

TOKEN_HEADER = "X-VideoStudio-Token"

# Declared so the OpenAPI docs (dev only) get an "Authorize" button.
_bearer = HTTPBearer(auto_error=False)


def _token_ok(expected: str, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(expected.encode(), (provided or "").encode())


def extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get(TOKEN_HEADER)


def require_token(
    request: Request, _credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> None:
    expected = request.app.state.services.settings.token
    if not _token_ok(expected, extract_token(request)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def websocket_token_ok(websocket: WebSocket) -> bool:
    expected = websocket.app.state.services.settings.token
    return _token_ok(expected, websocket.query_params.get("token"))
