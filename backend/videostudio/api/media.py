"""Serving generated media (videos, narration, thumbnails, uploads) to the webview.

``<video>``/``<audio>``/``<img>`` cannot send the auth header, so media URLs carry a
*media key* — an HMAC of the per-launch token that only grants read access to files
inside the data dir (the token itself never appears in URLs or access logs).
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from .services import Services

router = APIRouter(tags=["media"])

ALLOWED_TOP = ("projects", "cache", "assets")
mimetypes.add_type("text/vtt", ".vtt")
mimetypes.add_type("text/plain", ".ass")
mimetypes.add_type("application/x-subrip", ".srt")


def media_key(token: str) -> str:
    return hmac.new(token.encode(), b"videostudio-media", hashlib.sha256).hexdigest()[:32]


def media_url(svc: Services, rel_path: str) -> str:
    return f"/media/{quote(rel_path)}?k={media_key(svc.settings.token)}"


@router.get("/media/{rel_path:path}")
def get_media(rel_path: str, request: Request, k: str = Query(default="")) -> FileResponse:
    svc: Services = request.app.state.services
    if not hmac.compare_digest(k.encode(), media_key(svc.settings.token).encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid media key")
    root = svc.settings.data_dir.resolve()
    target = (root / Path(rel_path)).resolve()
    if root not in target.parents or target.relative_to(root).parts[0] not in ALLOWED_TOP:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if target.suffix.lower() == ".secret" or not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)
