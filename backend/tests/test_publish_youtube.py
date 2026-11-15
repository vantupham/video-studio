"""YouTube publishing against respx mocks: OAuth (PKCE loopback) + refresh, resumable upload
with resume, thumbnail / captions / playlist, quota, 401 and 5xx handling."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.request
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from videostudio.publishing import youtube as yt
from videostudio.publishing.oauth import GOOGLE_REVOKE_URL, GOOGLE_TOKEN_URL, YOUTUBE_API

from .publish_helpers import connect_account, make_approved_video, no_sleep, wait_pub

SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=SESSION1"
FRESH = {"access_token": "ya29.fresh-token", "refresh_token": "1//refresh-abc",
         "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(yt, "CHUNK", 256 * 1024)


def _configure_app(client, auth):
    r = client.put("/social/apps/youtube", headers=auth, json={
        "client_id": "123-abc.apps.googleusercontent.com",
        "client_secret": "GOCSPX-supersecretvalue9876", "testing_mode": True})
    assert r.status_code == 200
    return r.json()


class FakeUploadServer:
    """Implements the resumable-upload protocol: 308 + Range after each chunk."""

    def __init__(self, total: int, fail_puts: set[int] | None = None,
                 video: dict | None = None):
        self.total = total
        self.received = 0
        self.puts = 0
        self.fail_puts = fail_puts or set()
        self.ranges: list[str] = []
        self.video = video or {"id": "vid123", "status": {"uploadStatus": "uploaded",
                                                          "privacyStatus": "public"}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.puts += 1
        cr = request.headers["content-range"]
        self.ranges.append(cr)
        if cr.startswith("bytes */"):
            if self.received >= self.total:
                return httpx.Response(200, json=self.video)
            headers = {"Range": f"bytes=0-{self.received - 1}"} if self.received else {}
            return httpx.Response(308, headers=headers)
        if self.puts in self.fail_puts:
            return httpx.Response(503, json={"error": {"code": 503, "message": "backend",
                                                       "errors": [{"reason": "backendError"}]}})
        m = re.match(r"bytes (\d+)-(\d+)/(\d+)", cr)
        assert m, cr
        start, end = int(m.group(1)), int(m.group(2))
        assert start == self.received, (start, self.received)
        assert len(request.content) == end - start + 1
        self.received = end + 1
        if self.received >= self.total:
            return httpx.Response(201, json=self.video)
        return httpx.Response(308, headers={"Range": f"bytes=0-{end}"})


def _mock_post_steps(mock: respx.MockRouter) -> dict[str, respx.Route]:
    return {
        "thumb": mock.post(yt.THUMBNAIL_URL).mock(return_value=httpx.Response(200, json={})),
        "captions": mock.post(yt.CAPTIONS_URL).mock(return_value=httpx.Response(
            200, json={"id": "cap1"})),
        "playlist": mock.post(f"{YOUTUBE_API}/playlistItems").mock(
            return_value=httpx.Response(200, json={"id": "pli1"})),
    }


# ------------------------------------------------------------------ OAuth


def test_app_credentials_masked_and_never_in_sqlite(client: TestClient, auth, settings):
    cfg = _configure_app(client, auth)
    assert cfg["client_secret"] == "••••9876" and cfg["configured"]
    apps = client.get("/social/apps", headers=auth).json()
    assert "supersecret" not in json.dumps(apps)
    assert b"supersecretvalue" not in settings.db_path.read_bytes()
    # sending the masked value back keeps the secret
    client.put("/social/apps/youtube", headers=auth,
               json={"client_id": "new-id", "client_secret": "••••9876"})
    svc = client.app.state.services
    assert svc.secrets.get("socialapp:youtube:client_secret") == "GOCSPX-supersecretvalue9876"
    r = client.delete("/social/apps/youtube/secret", headers=auth).json()
    assert r["has_secret"] is False and not r["configured"]
    r = client.post("/social/youtube/connect", headers=auth, json={"open_browser": False})
    assert r.status_code == 422 and "Cài đặt" in r.json()["detail"]


def test_youtube_oauth_pkce_loopback_flow(client: TestClient, auth, settings):
    _configure_app(client, auth)
    flow = client.post("/social/youtube/connect", headers=auth,
                       json={"open_browser": False}).json()
    q = parse_qs(urlsplit(flow["auth_url"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
    assert "youtube.upload" in q["scope"][0] and "youtube.force-ssl" in q["scope"][0]
    redirect = q["redirect_uri"][0]
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/", redirect)
    with respx.mock(assert_all_called=True) as mock:
        token = mock.post(GOOGLE_TOKEN_URL).mock(return_value=httpx.Response(200, json={
            "access_token": "ya29.access", "refresh_token": "1//refresh-xyz",
            "expires_in": 3599, "scope": "youtube.upload youtube.force-ssl"}))
        mock.get(f"{YOUTUBE_API}/channels").mock(return_value=httpx.Response(200, json={
            "items": [{"id": "UCabc", "snippet": {"title": "Kênh Học Vui",
                                                  "customUrl": "@hocvui",
                                                  "thumbnails": {"default": {
                                                      "url": "https://yt3/a.jpg"}}},
                       "status": {"longUploadsStatus": "allowed"}}]}))
        # wrong state first → rejected without a token exchange
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{redirect}?code=x&state=evil", timeout=5)
        assert client.get(f"/social/oauth/{flow['id']}", headers=auth).json()[
            "status"] == "error"
        flow = client.post("/social/youtube/connect", headers=auth,
                           json={"open_browser": False}).json()
        q = parse_qs(urlsplit(flow["auth_url"]).query)
        redirect = q["redirect_uri"][0]
        with urllib.request.urlopen(f"{redirect}?code=4/0AUTHCODE&state={q['state'][0]}",
                                    timeout=10) as resp:
            assert "Đã kết nối" in resp.read().decode("utf-8")
        sent = parse_qs(token.calls.last.request.content.decode())
        assert sent["code"] == ["4/0AUTHCODE"] and sent["redirect_uri"] == [redirect]
        verifier = sent["code_verifier"][0]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                             ).rstrip(b"=").decode()
        assert challenge == q["code_challenge"][0]
    st = client.get(f"/social/oauth/{flow['id']}", headers=auth).json()
    assert st["status"] == "done" and len(st["account_ids"]) == 1
    accounts = client.get("/social/accounts", headers=auth).json()
    acc = next(a for a in accounts if a["id"] == st["account_ids"][0])
    assert acc["name"] == "Kênh Học Vui" and acc["external_id"] == "UCabc"
    # testing-mode apps: refresh token lasts 7 days → expiry warning shown
    assert acc["warning"] and "hết hạn" in acc["warning"]
    assert "refresh-xyz" not in json.dumps(accounts)
    db = settings.db_path.read_bytes()
    assert b"refresh-xyz" not in db and b"ya29.access" not in db
    svc = client.app.state.services
    assert json.loads(svc.secrets.get(f"social:{acc['id']}:token"))["refresh_token"] == \
        "1//refresh-xyz"

    with respx.mock as mock:
        revoke = mock.post(GOOGLE_REVOKE_URL).mock(return_value=httpx.Response(200))
        assert client.delete(f"/social/accounts/{acc['id']}", headers=auth).status_code == 204
        assert parse_qs(revoke.calls.last.request.content.decode())["token"] == [
            "1//refresh-xyz"]
    assert svc.secrets.get(f"social:{acc['id']}:token") is None


async def test_token_refresh_and_invalid_grant(client: TestClient, auth):
    _configure_app(client, auth)
    svc = client.app.state.services
    expired = {"access_token": "old", "refresh_token": "1//r",
               "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
    aid = connect_account(client, "youtube", "youtube", expired)
    with respx.mock as mock:
        route = mock.post(GOOGLE_TOKEN_URL).mock(side_effect=[
            httpx.Response(200, json={"access_token": "new-1", "expires_in": 3600}),
            httpx.Response(400, json={"error": "invalid_grant",
                                      "error_description": "Token has been expired"})])
        assert await svc.social.access_token(aid) == "new-1"
        assert await svc.social.access_token(aid) == "new-1"  # cached until expiry
        assert route.call_count == 1
        sent = parse_qs(route.calls.last.request.content.decode())
        assert sent["grant_type"] == ["refresh_token"] and sent["refresh_token"] == ["1//r"]
        from videostudio.publishing.base import PublishAuthError

        with pytest.raises(PublishAuthError, match="kết nối lại"):
            await svc.social.access_token(aid, force_refresh=True)
    acc = next(a for a in svc.social.list_accounts() if a["id"] == aid)
    assert acc["status"] == "expired" and acc["warning"]
    assert any("YouTube" in n["title"] for n in svc.publish.list_notifications())


# ------------------------------------------------------------------ upload


def test_resumable_upload_with_captions_thumbnail_playlist(client: TestClient, auth,
                                                          small_chunks):
    _configure_app(client, auth)
    video = make_approved_video(client)
    aid = connect_account(client, "youtube", "youtube", FRESH)
    server = FakeUploadServer(video["size"])
    with respx.mock(assert_all_called=False) as mock:
        start = mock.post(yt.UPLOAD_URL).mock(return_value=httpx.Response(
            200, headers={"Location": SESSION}))
        mock.put(SESSION).mock(side_effect=server)
        steps = _mock_post_steps(mock)
        poll = mock.get(f"{YOUTUBE_API}/videos").mock(return_value=httpx.Response(
            200, json={"items": [{"status": {"uploadStatus": "processed",
                                             "privacyStatus": "public"}}]}))
        check = client.post(f"/videos/{video['id']}/validate", headers=auth,
                            json={"account_id": aid}).json()
        assert check["ok"], check["issues"]
        assert any(i["code"] == "unverified_private" for i in check["issues"])
        r = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
            {"account_id": aid, "options": {"privacy": "public", "category_id": "27",
                                            "synthetic_media": True,
                                            "playlist_id": "PL123"}}]})
        assert r.status_code == 201, r.text
        pub = wait_pub(client, auth, r.json()[0]["id"], ("processing", "failed"))
        assert pub["status"] == "processing", pub
        body = json.loads(start.calls.last.request.content)
        assert body["snippet"]["title"] == "Quang hợp ở thực vật"
        assert body["snippet"]["description"].endswith("#SinhHoc")
        assert body["snippet"]["tags"] == ["sinh học", "quang hợp"]
        assert body["snippet"]["categoryId"] == "27"
        assert body["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                                  "containsSyntheticMedia": True, "embeddable": True}
        hdr = start.calls.last.request.headers
        assert hdr["x-upload-content-length"] == str(video["size"])
        assert hdr["authorization"] == "Bearer ya29.fresh-token"
        assert server.received == video["size"] and server.puts == 3  # 700 KB / 256 KiB
        cap = steps["captions"].calls.last.request
        assert "multipart/related" in cap.headers["content-type"]
        assert b'"videoId": "vid123"' in cap.content and b'"language": "vi"' in cap.content
        assert "Xin chào các bạn.".encode() in cap.content
        th = steps["thumb"].calls.last.request
        assert th.url.params["videoId"] == "vid123" and th.headers["content-type"] == \
            "image/jpeg"
        assert len(th.content) < 2 * 1024 * 1024
        pl = json.loads(steps["playlist"].calls.last.request.content)
        assert pl["snippet"]["playlistId"] == "PL123"
        assert pub["remote_id"] == "vid123"
        assert pub["remote_url"] == "https://www.youtube.com/watch?v=vid123"
        assert client.get(f"/videos/{video['id']}", headers=auth).json()["status"] == \
            "publishing"
        # status polling: processed → published
        client.post(f"/publications/{pub['id']}/poll", headers=auth)
        assert poll.called
    pub = client.get(f"/publications/{pub['id']}", headers=auth).json()
    assert pub["status"] == "published" and pub["published_at"]
    assert client.get(f"/videos/{video['id']}", headers=auth).json()["status"] == "published"


def test_upload_resumes_after_interruption(client: TestClient, auth, small_chunks):
    _configure_app(client, auth)
    delays = no_sleep(client)
    video = make_approved_video(client, thumbnail="none")
    aid = connect_account(client, "youtube", "youtube", FRESH)
    # 2nd PUT and every later one fails until the server "recovers"
    server = FakeUploadServer(video["size"], fail_puts=set(range(2, 2 + 40)))
    with respx.mock(assert_all_called=False) as mock:
        start = mock.post(yt.UPLOAD_URL).mock(return_value=httpx.Response(
            200, headers={"Location": SESSION}))
        mock.put(SESSION).mock(side_effect=server)
        _mock_post_steps(mock)
        r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                        json={"items": [{"account_id": aid,
                                         "options": {"upload_captions": False}}]})
        pub = wait_pub(client, auth, r.json()[0]["id"], ("retry_wait", "failed"))
        assert pub["status"] == "retry_wait" and pub["resumable"], pub
        assert server.received == 256 * 1024 and delays  # first chunk is on the server
        svc = client.app.state.services
        with svc.session_factory() as s:
            from videostudio.db import Publication

            row = s.get(Publication, pub["id"])
            assert row.upload_state["session_uri"] == SESSION
            assert row.upload_state["offset"] == 256 * 1024
        server.fail_puts = set()
        # scheduler picks the retry once next_attempt_at has passed
        svc.scheduler.tick(datetime.now(UTC) + timedelta(hours=1))
        pub = wait_pub(client, auth, pub["id"], ("processing", "failed"))
        assert pub["status"] == "processing"
        assert start.call_count == 1  # same session, no second upload
        assert server.received == video["size"]
        # query offset → then remaining chunks starting at 256 KiB
        assert any(r.startswith("bytes */") for r in server.ranges)
        assert f"bytes {256 * 1024}-" in " ".join(server.ranges)
        assert pub["attempts"] == 2


def test_quota_exceeded_waits_for_reset(client: TestClient, auth):
    _configure_app(client, auth)
    video = make_approved_video(client, thumbnail="none")
    aid = connect_account(client, "youtube", "youtube", FRESH)
    with respx.mock as mock:
        mock.post(yt.UPLOAD_URL).mock(return_value=httpx.Response(403, json={"error": {
            "code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}))
        r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                        json={"items": [{"account_id": aid}]})
        pub = wait_pub(client, auth, r.json()[0]["id"], ("retry_wait", "failed"))
    assert pub["status"] == "retry_wait" and "hạn mức" in pub["error"]
    when = datetime.fromisoformat(pub["next_attempt_at"])
    assert when > datetime.now(UTC) + timedelta(minutes=1)
    pacific = when.astimezone(datetime.now(UTC).astimezone().tzinfo)
    assert pacific  # reset time is shown in local time in the notification
    notes = client.get("/notifications", headers=auth).json()
    assert any("Tạm hoãn" in n["title"] for n in notes)


def test_401_refreshes_token_then_continues(client: TestClient, auth, small_chunks):
    _configure_app(client, auth)
    video = make_approved_video(client, thumbnail="none")
    aid = connect_account(client, "youtube", "youtube", {**FRESH, "access_token": "stale"})
    server = FakeUploadServer(video["size"])
    with respx.mock(assert_all_called=False) as mock:
        start = mock.post(yt.UPLOAD_URL).mock(side_effect=[
            httpx.Response(401, json={"error": {"code": 401, "message": "Invalid Credentials",
                                                "errors": [{"reason": "authError"}]}}),
            httpx.Response(200, headers={"Location": SESSION})])
        refresh = mock.post(GOOGLE_TOKEN_URL).mock(return_value=httpx.Response(
            200, json={"access_token": "ya29.after-refresh", "expires_in": 3600}))
        mock.put(SESSION).mock(side_effect=server)
        _mock_post_steps(mock)
        r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                        json={"items": [{"account_id": aid,
                                         "options": {"upload_captions": False}}]})
        pub = wait_pub(client, auth, r.json()[0]["id"], ("processing", "failed"))
    assert pub["status"] == "processing", pub
    assert refresh.call_count == 1
    assert start.calls[0].request.headers["authorization"] == "Bearer stale"
    assert start.calls[1].request.headers["authorization"] == "Bearer ya29.after-refresh"


def _wait_attempt(client, auth, pid: str, attempt: int, timeout: float = 20) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pub = client.get(f"/publications/{pid}", headers=auth).json()
        if pub["attempts"] == attempt and pub["status"] in ("retry_wait", "failed"):
            return pub
        time.sleep(0.05)
    raise AssertionError(f"attempt {attempt} not finished: {pub}")


def test_5xx_on_start_is_retried_with_backoff_then_fails(client: TestClient, auth):
    _configure_app(client, auth)
    video = make_approved_video(client, thumbnail="none")
    aid = connect_account(client, "youtube", "youtube", FRESH)
    svc = client.app.state.services
    with respx.mock as mock:
        mock.post(yt.UPLOAD_URL).mock(return_value=httpx.Response(500, json={"error": {
            "code": 500, "message": "boom", "errors": [{"reason": "backendError"}]}}))
        r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                        json={"items": [{"account_id": aid}]})
        pid = r.json()[0]["id"]
        waits = []
        for attempt in range(1, 6):
            pub = _wait_attempt(client, auth, pid, attempt)
            if pub["status"] == "failed":
                break
            waits.append(datetime.fromisoformat(pub["next_attempt_at"]) -
                         datetime.fromisoformat(pub["updated_at"]))
            svc.scheduler.tick(datetime.now(UTC) + timedelta(hours=2))
    assert pub["attempts"] == 5 and "tạm lỗi" in pub["error"]
    # exponential backoff: 30 s, 60 s, 120 s, 240 s
    secs = [round(w.total_seconds() / 10) * 10 for w in waits]
    assert secs[:4] == [30, 60, 120, 240]
    assert client.get(f"/videos/{video['id']}", headers=auth).json()["status"] == "failed"


def test_scheduled_publish_at_sets_private_and_status(client: TestClient, auth, small_chunks):
    _configure_app(client, auth)
    video = make_approved_video(client, thumbnail="none")
    aid = connect_account(client, "youtube", "youtube", FRESH)
    at = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    server = FakeUploadServer(video["size"], video={"id": "vidS", "status": {
        "uploadStatus": "uploaded", "privacyStatus": "private"}})
    with respx.mock(assert_all_called=False) as mock:
        start = mock.post(yt.UPLOAD_URL).mock(return_value=httpx.Response(
            200, headers={"Location": SESSION}))
        mock.put(SESSION).mock(side_effect=server)
        _mock_post_steps(mock)
        r = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
            {"account_id": aid, "schedule": {"mode": "platform", "at": at.isoformat()},
             "options": {"upload_captions": False, "made_for_kids": True}}]})
        assert r.status_code == 201, r.text
        pub = wait_pub(client, auth, r.json()[0]["id"], ("scheduled_remote", "failed"))
    status = json.loads(start.calls.last.request.content)["status"]
    assert status["privacyStatus"] == "private" and status["selfDeclaredMadeForKids"] is True
    assert status["publishAt"] == at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    assert pub["status"] == "scheduled_remote"
    assert client.get(f"/videos/{video['id']}", headers=auth).json()["status"] == "scheduled"


def test_youtube_constraint_errors(client: TestClient, auth):
    video = make_approved_video(client, thumbnail="none")
    aid = connect_account(client, "youtube", "youtube", FRESH)
    client.put(f"/videos/{video['id']}/publish-meta", headers=auth, json={
        "youtube": {"title": "Tiêu đề <sai>" + "x" * 100, "tags": ["a" * 300, "b" * 300]}})
    check = client.post(f"/videos/{video['id']}/validate", headers=auth,
                        json={"account_id": aid}).json()
    codes = {i["code"] for i in check["issues"] if i["level"] == "error"}
    assert {"title_len", "title_chars", "tags_len"} <= codes and not check["ok"]
    r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                    json={"items": [{"account_id": aid}]})
    assert r.status_code == 422 and r.json()["detail"]["issues"][aid]
