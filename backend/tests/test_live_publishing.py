"""Live checks against the real YouTube / Facebook APIs with the user's own app credentials.

Read-only by default (token refresh, channel / page lookup, token permissions)::

    YOUTUBE_CLIENT_ID=… YOUTUBE_CLIENT_SECRET=… YOUTUBE_REFRESH_TOKEN=…
    FACEBOOK_APP_ID=… FACEBOOK_APP_SECRET=… FACEBOOK_PAGE_TOKEN=…
    uv run pytest tests/test_live_publishing.py -v

``VIDEOSTUDIO_LIVE_YOUTUBE_UPLOAD=1`` additionally uploads a 3-second test video as
**private** to the channel (costs ~1 600 quota units; delete it afterwards in YouTube Studio).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from videostudio.db import Render
from videostudio.publishing import oauth

from .publish_helpers import connect_account, make_approved_video, wait_pub

pytestmark = pytest.mark.live


def env(*names: str) -> dict[str, str]:
    values = {n: os.environ.get(n, "") for n in names}
    if not all(values.values()):
        pytest.skip(f"missing {', '.join(n for n, v in values.items() if not v)}")
    return values


YT_VARS = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN")
FB_VARS = ("FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET", "FACEBOOK_PAGE_TOKEN")


async def test_youtube_live_refresh_and_channel():
    e = env(*YT_VARS)
    fresh = await oauth.google_refresh(e["YOUTUBE_CLIENT_ID"], e["YOUTUBE_CLIENT_SECRET"],
                                       e["YOUTUBE_REFRESH_TOKEN"])
    assert fresh["access_token"]
    channel = await oauth.youtube_channel(fresh["access_token"])
    assert channel["id"].startswith("UC")


async def test_facebook_live_page_token():
    e = env(*FB_VARS)
    info = await oauth.facebook_debug_token(e["FACEBOOK_PAGE_TOKEN"], e["FACEBOOK_APP_ID"],
                                            e["FACEBOOK_APP_SECRET"])
    assert info.get("is_valid"), info
    assert info.get("type") == "PAGE", "FACEBOOK_PAGE_TOKEN must be a Page token"
    assert "pages_manage_posts" in info.get("scopes", [])
    page = await oauth._fb_get("me", {"fields": "id,name",
                                      "access_token": e["FACEBOOK_PAGE_TOKEN"]},
                               "Facebook")
    assert page["id"]


def test_youtube_live_private_upload(client: TestClient, auth, tmp_path):
    e = env(*YT_VARS)
    if os.environ.get("VIDEOSTUDIO_LIVE_YOUTUBE_UPLOAD") != "1":
        pytest.skip("set VIDEOSTUDIO_LIVE_YOUTUBE_UPLOAD=1 to upload a private test video")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    client.put("/social/apps/youtube", headers=auth, json={
        "client_id": e["YOUTUBE_CLIENT_ID"], "client_secret": e["YOUTUBE_CLIENT_SECRET"]})
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    aid = connect_account(client, "youtube", "youtube", {
        "access_token": "expired", "refresh_token": e["YOUTUBE_REFRESH_TOKEN"],
        "expires_at": expired})
    video = make_approved_video(client, duration=3.0,
                                title="Video Studio – kiểm thử tải lên (riêng tư)")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=1280x720:rate=30:duration=3", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=3", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(video["video_path"])], check=True)
    svc = client.app.state.services
    with svc.session_factory.begin() as s:
        s.get(Render, video["render_id"]).size_bytes = video["video_path"].stat().st_size
    r = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
        {"account_id": aid, "options": {"privacy": "private", "notify_subscribers": False,
                                        "synthetic_media": True}}]})
    assert r.status_code == 201, r.text
    pub = wait_pub(client, auth, r.json()[0]["id"], ("processing", "published", "failed"),
                   timeout=300)
    assert pub["status"] in ("processing", "published"), pub
    assert pub["remote_url"].startswith("https://www.youtube.com/watch?v=")
