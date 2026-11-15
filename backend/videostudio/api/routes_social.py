"""Social accounts: developer-app credentials, OAuth connect flows, disconnect."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from videostudio.publishing.accounts import SocialError

from .services import Services, get_services

router = APIRouter(prefix="/social", tags=["social"])


class AppConfigIn(BaseModel):
    client_id: str | None = Field(default=None, max_length=512)
    client_secret: str | None = Field(default=None, max_length=1024)
    testing_mode: bool | None = None


class YouTubeConnect(BaseModel):
    open_browser: bool = True


class FacebookComplete(BaseModel):
    flow_id: str
    url: str = Field(max_length=8192)


class FacebookPages(BaseModel):
    flow_id: str
    page_ids: list[str]


class MockConnect(BaseModel):
    platform: str
    name: str | None = None


def _err(exc: SocialError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


@router.get("/apps")
def list_apps(svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return [svc.social.app_config(p) for p in ("youtube", "facebook")]


@router.put("/apps/{platform}")
def put_app(platform: str, body: AppConfigIn,
            svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.social.set_app_config(platform, body.client_id, body.client_secret,
                                         body.testing_mode)
    except SocialError as exc:
        raise _err(exc) from exc


@router.delete("/apps/{platform}/secret")
def clear_app_secret(platform: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.social.clear_app_secret(platform)
    except SocialError as exc:
        raise _err(exc) from exc


@router.get("/accounts")
def list_accounts(svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return svc.social.list_accounts()


@router.post("/accounts/mock", status_code=201)
def connect_mock(body: MockConnect, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.social.connect_mock(body.platform, body.name)
    except SocialError as exc:
        raise _err(exc) from exc


@router.delete("/accounts/{account_id}", status_code=204)
async def disconnect(account_id: str, revoke: bool = True,
                     svc: Services = Depends(get_services)) -> None:
    active = [p for p in svc.publish.list() if p["account_id"] == account_id
              and p["status"] in ("queued", "uploading", "retry_wait", "scheduled")]
    if active:
        raise HTTPException(409, f"Tài khoản còn {len(active)} bài đang chờ đăng; huỷ trước khi "
                                 "ngắt kết nối")
    try:
        await svc.social.disconnect(account_id, revoke=revoke)
    except SocialError as exc:
        raise _err(exc) from exc


@router.post("/youtube/connect")
def youtube_connect(body: YouTubeConnect | None = None,
                    svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.social.start_youtube(open_browser=body.open_browser if body else True)
    except SocialError as exc:
        raise _err(exc) from exc


@router.get("/oauth/{flow_id}")
def oauth_flow(flow_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.social.flow(flow_id).public()
    except SocialError as exc:
        raise _err(exc) from exc


@router.post("/facebook/connect")
def facebook_connect(svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return svc.social.start_facebook()
    except SocialError as exc:
        raise _err(exc) from exc


@router.post("/facebook/complete")
async def facebook_complete(body: FacebookComplete,
                            svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return await svc.social.complete_facebook(body.flow_id, body.url)
    except SocialError as exc:
        raise _err(exc) from exc


@router.post("/facebook/pages")
async def facebook_pages(body: FacebookPages,
                         svc: Services = Depends(get_services)) -> dict[str, Any]:
    try:
        return await svc.social.select_facebook_pages(body.flow_id, body.page_ids)
    except SocialError as exc:
        raise _err(exc) from exc


@router.get("/publishers")
def publishers(svc: Services = Depends(get_services)) -> list[dict[str, Any]]:
    return [p.public() for p in svc.registry.publishers.values()]
