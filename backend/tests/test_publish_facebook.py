"""Facebook Page publishing against respx mocks: login code → long-lived token → page
tokens, chunked upload (start/transfer/finish) incl. scheduling, captions, thumbnail,
Reels via rupload, token errors and transient errors."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from videostudio.publishing import facebook as fb
from videostudio.publishing.oauth import FB_GRAPH, FB_REDIRECT_URI

from .publish_helpers import connect_account, make_approved_video, no_sleep, wait_pub

PAGE = "1234567890"
PAGE_TOKEN = {"access_token": "EAAPAGE-token-xyz"}


def _configure(client, auth):
    r = client.put("/social/apps/facebook", headers=auth,
                   json={"client_id": "998877", "client_secret": "fb-app-secret-4321"})
    assert r.json()["configured"] and r.json()["client_secret"] == "••••4321"


def _form(request: httpx.Request) -> dict[str, str]:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/x-www-form-urlencoded"):
        return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    # multipart: pull simple fields
    out = {}
    boundary = ctype.split("boundary=")[1].encode()
    for part in request.content.split(b"--" + boundary):
        if b'name="' not in part:
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        body = body.rstrip(b"\r\n")
        out[name] = body.decode("utf-8", "replace") if b"filename=" not in head \
            else f"<file {len(body)} bytes>"
    return out


class FakeGraphVideo:
    """start → transfer chunks of ``chunk`` bytes → finish."""

    def __init__(self, total: int, chunk: int = 300_000, fail_transfer: int = 0):
        self.total = total
        self.chunk = chunk
        self.received = 0
        self.fail_transfer = fail_transfer
        self.finish: dict[str, str] = {}
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        f = _form(request)
        phase = f["upload_phase"]
        self.calls.append(phase)
        assert f["access_token"] == PAGE_TOKEN["access_token"]
        if phase == "start":
            assert int(f["file_size"]) == self.total
            return httpx.Response(200, json={"video_id": "v777", "upload_session_id": "sess1",
                                             "start_offset": "0",
                                             "end_offset": str(min(self.chunk, self.total))})
        if phase == "transfer":
            if self.fail_transfer:
                self.fail_transfer -= 1
                return httpx.Response(500, json={"error": {"message": "Temporary",
                                                           "code": 2, "is_transient": True}})
            assert int(f["start_offset"]) == self.received
            size = int(f["video_file_chunk"].split()[1])
            self.received += size
            end = min(self.received + self.chunk, self.total)
            return httpx.Response(200, json={"start_offset": str(self.received),
                                             "end_offset": str(end)})
        self.finish = f
        return httpx.Response(200, json={"success": True})


def test_facebook_login_pages_and_token_storage(client: TestClient, auth, settings):
    _configure(client, auth)
    flow = client.post("/social/facebook/connect", headers=auth).json()
    q = parse_qs(urlsplit(flow["auth_url"]).query)
    assert q["redirect_uri"] == [FB_REDIRECT_URI] and q["client_id"] == ["998877"]
    assert "pages_manage_posts" in q["scope"][0]
    state = q["state"][0]
    with respx.mock(assert_all_called=True) as mock:
        tok = mock.get(f"{FB_GRAPH}/oauth/access_token").mock(side_effect=[
            httpx.Response(200, json={"access_token": "short-lived", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "LONG-user-token",
                                      "expires_in": 5_184_000})])
        mock.get(f"{FB_GRAPH}/me/accounts").mock(return_value=httpx.Response(200, json={
            "data": [{"id": PAGE, "name": "Trang Học Vui", "access_token": "EAAPAGE1",
                      "tasks": ["CREATE_CONTENT", "MANAGE"], "category": "Education",
                      "picture": {"data": {"url": "https://fb/p.jpg"}}},
                     {"id": "555", "name": "Trang Khác", "access_token": "EAAPAGE2",
                      "tasks": ["CREATE_CONTENT"]}]}))
        debug = mock.get(f"{FB_GRAPH}/debug_token").mock(return_value=httpx.Response(
            200, json={"data": {"expires_at": 0,
                                "data_access_expires_at": int(
                                    (datetime.now(UTC) + timedelta(days=90)).timestamp())}}))
        # the webview captured: login_success.html?code=…&state=…#_=_
        r = client.post("/social/facebook/complete", headers=auth, json={
            "flow_id": flow["id"],
            "url": f"{FB_REDIRECT_URI}?code=AQCODE123&state={state}#_=_"})
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["status"] == "select_pages"
        assert [p["name"] for p in got["pages"]] == ["Trang Học Vui", "Trang Khác"]
        assert "EAAPAGE1" not in r.text  # page tokens never leave the backend
        first = tok.calls[0].request.url.params
        assert first["code"] == "AQCODE123" and first["redirect_uri"] == FB_REDIRECT_URI
        assert tok.calls[1].request.url.params["grant_type"] == "fb_exchange_token"
        assert tok.calls[1].request.url.params["fb_exchange_token"] == "short-lived"
        r = client.post("/social/facebook/pages", headers=auth,
                        json={"flow_id": flow["id"], "page_ids": [PAGE]})
        assert r.status_code == 200 and r.json()["status"] == "done"
        assert debug.calls.last.request.url.params["access_token"] == "998877|fb-app-secret-4321"
    acc = client.get("/social/accounts", headers=auth).json()[0]
    assert acc["name"] == "Trang Học Vui" and acc["kind"] == "page"
    assert acc["token_expires_at"] and acc["warning"] is None  # 90 days left
    svc = client.app.state.services
    assert json.loads(svc.secrets.get(f"social:{acc['id']}:token"))["access_token"] == \
        "EAAPAGE1"
    assert svc.secrets.get(f"social:{acc['id']}:user_token") == "LONG-user-token"
    db = settings.db_path.read_bytes()
    assert b"EAAPAGE1" not in db and b"LONG-user-token" not in db and b"fb-app-secret" not in db


def test_facebook_complete_rejects_bad_state_and_denial(client: TestClient, auth):
    _configure(client, auth)
    flow = client.post("/social/facebook/connect", headers=auth).json()
    r = client.post("/social/facebook/complete", headers=auth, json={
        "flow_id": flow["id"], "url": f"{FB_REDIRECT_URI}?code=X&state=wrong"})
    assert r.status_code == 400 and "state" in r.json()["detail"]
    r = client.post("/social/facebook/complete", headers=auth, json={
        "flow_id": flow["id"],
        "url": f"{FB_REDIRECT_URI}?error=access_denied&error_reason=user_denied"})
    assert r.status_code == 400 and "huỷ" in r.json()["detail"]
    assert client.post("/social/facebook/complete", headers=auth,
                       json={"flow_id": "nope", "url": "x"}).status_code == 404


def test_page_video_chunked_upload_scheduled(client: TestClient, auth):
    video = make_approved_video(client, size=700_000)
    aid = connect_account(client, "facebook", "facebook_page", PAGE_TOKEN, external_id=PAGE,
                          name="Trang Học Vui")
    graph = FakeGraphVideo(video["size"])
    at = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{fb.GRAPH_VIDEO}/{PAGE}/videos").mock(side_effect=graph)
        caps = mock.post(f"{FB_GRAPH}/v777/captions").mock(
            return_value=httpx.Response(200, json={"success": True}))
        thumb = mock.post(f"{FB_GRAPH}/v777/thumbnails").mock(
            return_value=httpx.Response(200, json={"success": True}))
        r = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
            {"account_id": aid, "schedule": {"mode": "platform", "at": at.isoformat()}}]})
        assert r.status_code == 201, r.text
        pub = wait_pub(client, auth, r.json()[0]["id"], ("scheduled_remote", "failed"))
    assert pub["status"] == "scheduled_remote", pub
    assert graph.calls == ["start", "transfer", "transfer", "transfer", "finish"]
    assert graph.received == video["size"]
    assert graph.finish["published"] == "false"
    assert graph.finish["scheduled_publish_time"] == str(int(at.timestamp()))
    assert graph.finish["title"] == "Quang hợp ở thực vật"
    assert "#SinhHoc" in graph.finish["description"]
    cap = _form(caps.calls.last.request)
    assert cap["default_locale"] == "vi_VN" and cap["captions_file"].startswith("<file")
    assert b'filename="captions.vi_VN.srt"' in caps.calls.last.request.content
    assert _form(thumb.calls.last.request)["is_preferred"] == "true"
    assert pub["remote_url"] == f"https://www.facebook.com/{PAGE}/videos/v777"
    assert client.get(f"/videos/{video['id']}", headers=auth).json()["status"] == "scheduled"


def test_page_video_transient_error_retried_in_place(client: TestClient, auth):
    delays = no_sleep(client)
    video = make_approved_video(client, size=400_000, thumbnail="none")
    aid = connect_account(client, "facebook", "facebook_page", PAGE_TOKEN, external_id=PAGE)
    graph = FakeGraphVideo(video["size"], fail_transfer=2)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{fb.GRAPH_VIDEO}/{PAGE}/videos").mock(side_effect=graph)
        mock.post(f"{FB_GRAPH}/v777/captions").mock(return_value=httpx.Response(200, json={}))
        mock.get(f"{FB_GRAPH}/v777").mock(return_value=httpx.Response(200, json={
            "status": {"video_status": "ready"}, "published": True,
            "permalink_url": f"/{PAGE}/videos/v777/"}))
        r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                        json={"items": [{"account_id": aid}]})
        pub = wait_pub(client, auth, r.json()[0]["id"], ("processing", "failed"))
        assert pub["status"] == "processing" and pub["attempts"] == 1
        assert delays[:2] == [2, 4] and graph.received == video["size"]
        assert graph.finish["published"] == "true"
        client.post(f"/publications/{pub['id']}/poll", headers=auth)
    pub = client.get(f"/publications/{pub['id']}", headers=auth).json()
    assert pub["status"] == "published"
    assert pub["remote_url"] == f"https://www.facebook.com/{PAGE}/videos/v777/"


def test_expired_page_token_fails_without_retry(client: TestClient, auth):
    video = make_approved_video(client, thumbnail="none")
    aid = connect_account(client, "facebook", "facebook_page", PAGE_TOKEN, external_id=PAGE)
    with respx.mock as mock:
        mock.post(f"{fb.GRAPH_VIDEO}/{PAGE}/videos").mock(return_value=httpx.Response(
            400, json={"error": {"message": "Error validating access token", "code": 190,
                                 "type": "OAuthException"}}))
        r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                        json={"items": [{"account_id": aid}]})
        pub = wait_pub(client, auth, r.json()[0]["id"], ("failed", "retry_wait"))
    assert pub["status"] == "failed" and "kết nối lại" in pub["error"]
    assert pub["attempts"] == 1


def test_reels_requires_vertical_and_uploads_via_rupload(client: TestClient, auth):
    video = make_approved_video(client, size=300_000, duration=30, thumbnail="none",
                                extra_renders=["9:16"])
    aid = connect_account(client, "facebook", "facebook_page", PAGE_TOKEN, external_id=PAGE)
    check = client.post(f"/videos/{video['id']}/validate", headers=auth,
                        json={"account_id": aid, "options": {"format": "reels"}}).json()
    assert check["ok"] and check["render"]["aspect"] == "9:16"  # picks the 9:16 variant
    received = {"n": 0}

    def rupload(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"OAuth {PAGE_TOKEN['access_token']}"
        assert int(request.headers["offset"]) == received["n"]
        assert int(request.headers["file_size"]) == video["size"]
        received["n"] += len(request.content)
        return httpx.Response(200, json={"success": True})

    with respx.mock as mock:
        reels = mock.post(f"{FB_GRAPH}/{PAGE}/video_reels").mock(side_effect=[
            httpx.Response(200, json={"video_id": "r42", "upload_url": "x"}),
            httpx.Response(200, json={"success": True})])
        mock.post(f"{fb.RUPLOAD}/r42").mock(side_effect=rupload)
        r = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
            {"account_id": aid, "options": {"format": "reels"}}]})
        pub = wait_pub(client, auth, r.json()[0]["id"], ("processing", "failed"))
    assert pub["status"] == "processing", pub
    assert received["n"] == video["size"]
    finish = _form(reels.calls.last.request)
    assert finish["upload_phase"] == "finish" and finish["video_state"] == "PUBLISHED"
    assert pub["remote_url"] == "https://www.facebook.com/reel/r42"
    assert pub["meta"]["aspect"] == "9:16"


def test_reels_aspect_mismatch_offers_rerender(client: TestClient, auth):
    video = make_approved_video(client, thumbnail="none")
    aid = connect_account(client, "facebook", "facebook_page", PAGE_TOKEN, external_id=PAGE)
    check = client.post(f"/videos/{video['id']}/validate", headers=auth,
                        json={"account_id": aid, "options": {"format": "reels"}}).json()
    issue = next(i for i in check["issues"] if i["code"] == "aspect")
    assert issue["level"] == "error"
    assert issue["fix"] == {"action": "rerender_aspect", "aspect": "9:16"}
    soon = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    check = client.post(f"/videos/{video['id']}/validate", headers=auth, json={
        "account_id": aid, "schedule": {"mode": "platform", "at": soon}}).json()
    assert any(i["code"] == "schedule_too_soon" for i in check["issues"])


@pytest.mark.parametrize("url", [
    f"{FB_REDIRECT_URI}#access_token=abc&state=s",
    f"{FB_REDIRECT_URI}?code=c1&state=s#_=_"])
def test_parse_redirect(url):
    from videostudio.publishing.oauth import parse_redirect

    p = parse_redirect(url)
    assert p["state"] == "s"
