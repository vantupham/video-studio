"""Publishing service: idempotency, resume, local scheduling, missed schedules across a
restart, queue pause, validation rules, mock posts, thumbnails, metadata suggestions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from videostudio.api import create_app
from videostudio.publishing.base import PublishConstraints
from videostudio.publishing.validation import validate

from .publish_helpers import make_approved_video, no_sleep, wait_pub
from .test_api import wait_job
from .test_pipeline_api import requires_ffmpeg


def _mock_account(client, auth, platform="youtube") -> str:
    r = client.post("/social/accounts/mock", headers=auth, json={"platform": platform})
    assert r.status_code == 201
    return r.json()["id"]


# ------------------------------------------------------------------ validation rules


def _c(**kw) -> PublishConstraints:
    return PublishConstraints(platform="X", **kw)


def test_validation_rules():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    video = {"aspect": "16:9", "duration_s": 120, "size_bytes": 50_000_000}
    ok = validate(_c(aspect_ratios=["16:9"], max_duration_s=300), video=video,
                  meta={"title": "A"}, now=now)
    assert ok == []
    issues = validate(_c(aspect_ratios=["9:16"], preferred_aspect="9:16", max_duration_s=90,
                         min_duration_s=3, max_file_size_mb=10, title_required=True,
                         max_title_len=5, title_forbidden_chars="<>",
                         max_description_bytes=10, max_tags_chars=10),
                      video=video, meta={"title": "Dài quá <b>", "description": "ếếếếế",
                                         "tags": ["một hai", "ba bốn năm"]}, now=now,
                      other_aspects=["9:16"])
    codes = {i["code"]: i for i in issues}
    assert codes["aspect"]["fix"] == {"action": "use_variant", "aspect": "9:16"}
    assert {"duration_max", "size", "title_len", "title_chars", "description_bytes",
            "tags_len"} <= set(codes)
    assert "2 phút" in codes["duration_max"]["message"]
    # schedules
    c = _c(supports_scheduling=True, min_schedule_ahead_s=600, max_schedule_ahead_s=86400)
    soon = validate(c, video=video, meta={}, schedule={"mode": "platform",
                                                       "at": now + timedelta(minutes=5)},
                    now=now)
    assert [i["code"] for i in soon] == ["schedule_too_soon"]
    far = validate(c, video=video, meta={}, schedule={"mode": "platform",
                                                      "at": now + timedelta(days=3)}, now=now)
    assert [i["code"] for i in far] == ["schedule_too_far"]
    past = validate(c, video=video, meta={}, schedule={"mode": "local",
                                                       "at": now - timedelta(minutes=1)},
                    now=now)
    assert [i["code"] for i in past] == ["schedule_past"]
    unsupported = validate(_c(), video=video, meta={}, now=now,
                           schedule={"mode": "platform", "at": now + timedelta(hours=1)})
    assert [i["code"] for i in unsupported] == ["schedule_unsupported"]


def test_publish_requires_approval(client: TestClient, auth, session_factory):
    from videostudio.db import Storyboard

    video = make_approved_video(client, thumbnail="none")
    aid = _mock_account(client, auth)
    with session_factory.begin() as s:
        s.get(Storyboard, video["id"]).status = "in_review"
    r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                    json={"items": [{"account_id": aid}]})
    assert r.status_code == 422 and "duyệt" in r.json()["detail"]["message"]


# ------------------------------------------------------------------ mock publishing


def test_mock_publish_now_idempotent_and_post_page(client: TestClient, auth):
    video = make_approved_video(client)
    yt, fbk = _mock_account(client, auth, "youtube"), _mock_account(client, auth, "facebook")
    accounts = client.get("/social/accounts", headers=auth).json()
    assert {a["publisher_id"] for a in accounts} == {"mock_youtube", "mock_facebook"}
    r = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
        {"account_id": yt, "options": {"privacy": "unlisted"}}, {"account_id": fbk}]})
    assert r.status_code == 201, r.text
    pubs = [wait_pub(client, auth, p["id"], ("published", "failed")) for p in r.json()]
    assert [p["status"] for p in pubs] == ["published", "published"]
    v = client.get(f"/videos/{video['id']}", headers=auth).json()
    assert v["status"] == "published"
    actions = [e["action"] for e in v["events"]]
    assert "publication_published" in actions and "publication_created" in actions
    url = pubs[0]["remote_url"]
    assert url.startswith("/mock-posts/yo-") and "?k=" in url
    page = client.get(url)
    assert page.status_code == 200 and "Quang hợp ở thực vật" in page.text
    assert "GIẢ LẬP" in page.text and "unlisted" in page.text and "/media/" in page.text
    assert client.get(url.split("?")[0]).status_code == 403

    # running the job again does not post a second time
    svc = client.app.state.services
    job = svc.queue.enqueue("publish", {"publication_id": pubs[0]["id"]})
    done = wait_job(client, auth, job["id"])
    assert done["result"]["skipped"] == "published"
    posts = list((svc.settings.data_dir / "mock_posts").glob("*.json"))
    assert len(posts) == 2
    # a second publication to the same account is refused unless explicitly a repost
    r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                    json={"items": [{"account_id": yt}]})
    assert r.status_code == 422
    assert r.json()["detail"]["issues"][yt][0]["code"] == "duplicate"
    r = client.post(f"/videos/{video['id']}/publications", headers=auth,
                    json={"items": [{"account_id": yt, "allow_repost": True}]})
    assert r.status_code == 201
    notes = client.get("/notifications", headers=auth).json()
    assert sum("Đã đăng" in n["title"] for n in notes) == 2


def test_interrupted_upload_resumes_from_offset(client: TestClient, auth):
    no_sleep(client)
    video = make_approved_video(client, size=3_000_000, thumbnail="none")
    yt = _mock_account(client, auth)
    r = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
        {"account_id": yt, "options": {"mock_fail_at": 0.5}}]})
    pub = wait_pub(client, auth, r.json()[0]["id"], ("retry_wait", "failed"))
    assert pub["status"] == "retry_wait" and pub["resumable"]
    svc = client.app.state.services
    from videostudio.db import Publication

    with svc.session_factory() as s:
        state = s.get(Publication, pub["id"]).upload_state
    assert 1_400_000 <= state["offset"] < 3_000_000 and state["mock_failed"]
    svc.scheduler.tick(datetime.now(UTC) + timedelta(minutes=5))
    pub = wait_pub(client, auth, pub["id"], ("published", "failed"))
    assert pub["status"] == "published" and pub["attempts"] == 2
    job = client.get(f"/jobs/{pub['job_id']}", headers=auth).json()
    assert any("Tiếp tục phiên" in entry["message"] for entry in job["logs"])
    import json

    post = json.loads((svc.settings.data_dir / "mock_posts" / f"{pub['remote_id']}.json")
                      .read_text(encoding="utf-8"))
    import hashlib

    assert post["sha256"] == hashlib.sha256(video["video_path"].read_bytes()).hexdigest()


def test_local_schedule_runs_when_due_and_cancel(client: TestClient, auth):
    video = make_approved_video(client, thumbnail="none")
    yt, fbk = _mock_account(client, auth, "youtube"), _mock_account(client, auth, "facebook")
    at = datetime.now(UTC) + timedelta(minutes=1)
    r = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
        {"account_id": yt, "schedule": {"mode": "local", "at": at.isoformat()}},
        {"account_id": fbk, "schedule": {"mode": "local", "at": at.isoformat()}}]})
    assert r.status_code == 201, r.text
    p_yt, p_fb = r.json()
    assert p_yt["status"] == "scheduled"
    v = client.get(f"/videos/{video['id']}", headers=auth).json()
    assert v["status"] == "scheduled" and v["render_locked"]
    assert client.post(f"/storyboards/{video['id']}/render", headers=auth,
                       json={}).status_code == 409
    svc = client.app.state.services
    assert svc.scheduler.tick()["started"] == []  # not due yet
    client.post(f"/publications/{p_fb['id']}/cancel", headers=auth)
    out = svc.scheduler.tick(at + timedelta(seconds=1))
    assert out["started"] == [p_yt["id"]] and out["awake"] in (True, False)
    pub = wait_pub(client, auth, p_yt["id"], ("published", "failed"))
    assert pub["status"] == "published"
    assert client.get(f"/publications/{p_fb['id']}", headers=auth).json()["status"] == \
        "cancelled"
    assert client.get(f"/videos/{video['id']}", headers=auth).json()["status"] == "published"
    assert client.post(f"/publications/{pub['id']}/cancel", headers=auth).status_code == 409


def test_queue_pause_holds_due_publications(client: TestClient, auth):
    video = make_approved_video(client, thumbnail="none")
    yt = _mock_account(client, auth)
    at = datetime.now(UTC) + timedelta(minutes=2)
    pid = client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
        {"account_id": yt, "schedule": {"mode": "local", "at": at.isoformat()}}]}).json()[0]["id"]
    assert client.put("/queue/state", headers=auth, json={"paused": True}).json()["paused"]
    svc = client.app.state.services
    assert svc.queue.paused
    assert svc.scheduler.tick(at + timedelta(seconds=5))["started"] == []
    client.put("/queue/state", headers=auth, json={"paused": False})
    assert svc.scheduler.tick(at + timedelta(seconds=5))["started"] == [pid]
    assert wait_pub(client, auth, pid, ("published", "failed"))["status"] == "published"


def test_missed_schedule_detected_after_restart(settings, auth):
    app = create_app(settings)
    with TestClient(app) as client:
        video = make_approved_video(client, thumbnail="none")
        yt = _mock_account(client, auth)
        fbk = _mock_account(client, auth, "facebook")
        at = datetime.now(UTC) + timedelta(minutes=1)
        ids = [p["id"] for p in client.post(
            f"/videos/{video['id']}/publications", headers=auth, json={"items": [
                {"account_id": a, "schedule": {"mode": "local", "at": at.isoformat()}}
                for a in (yt, fbk)]}).json()]
    # the app was closed past the scheduled time
    from videostudio.db import Publication, make_engine, make_session_factory

    engine = make_engine(settings.db_path)
    with make_session_factory(engine).begin() as s:
        for pid in ids:
            s.get(Publication, pid).scheduled_at = datetime.now(UTC) - timedelta(hours=3)
    engine.dispose()
    with TestClient(create_app(settings)) as client:
        missed = client.get("/schedule/missed", headers=auth).json()
        assert sorted(p["id"] for p in missed) == sorted(ids)
        assert all(p["status"] == "missed" for p in missed)
        notes = client.get("/notifications", headers=auth).json()
        assert any("lỡ giờ" in n["title"] for n in notes)
        v = client.get(f"/videos/{video['id']}", headers=auth).json()
        assert v["status"] == "scheduled"
        assert any(e["action"] == "publication_missed" for e in v["events"])
        # the scheduler must not silently publish missed posts
        client.app.state.services.scheduler.tick()
        assert client.get(f"/publications/{ids[0]}", headers=auth).json()["status"] == "missed"
        r = client.post("/schedule/missed/resolve", headers=auth,
                        json={"ids": [ids[0]], "action": "publish_now"})
        assert r.status_code == 200
        assert wait_pub(client, auth, ids[0], ("published", "failed"))["status"] == "published"
        later = datetime.now(UTC) + timedelta(hours=5)
        r = client.post("/schedule/missed/resolve", headers=auth,
                        json={"ids": [ids[1]], "action": "reschedule",
                              "at": later.isoformat()}).json()
        assert r[0]["status"] == "scheduled"
        assert client.get("/schedule/missed", headers=auth).json() == []
        assert datetime.fromisoformat(client.get("/schedule/status", headers=auth).json()[
            "next_local"]) == later.replace(microsecond=later.microsecond)


def test_skip_missed(settings, auth):
    with TestClient(create_app(settings)) as client:
        video = make_approved_video(client, thumbnail="none")
        yt = _mock_account(client, auth)
        pid = client.post(f"/videos/{video['id']}/publications", headers=auth, json={
            "items": [{"account_id": yt, "schedule": {
                "mode": "local",
                "at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}}]}).json()[0]["id"]
        svc = client.app.state.services
        svc.publish.detect_missed(now=datetime.now(UTC) + timedelta(hours=1))
        r = client.post("/schedule/missed/resolve", headers=auth,
                        json={"ids": [pid], "action": "skip"}).json()
        assert r[0]["status"] == "cancelled"
        assert client.get(f"/videos/{video['id']}", headers=auth).json()["status"] == \
            "approved"


# ------------------------------------------------------------------ misc endpoints


def test_desktop_prefs_and_publishers(client: TestClient, auth):
    prefs = client.get("/settings/desktop", headers=auth).json()
    assert prefs["close_to_tray"] is True and prefs["autostart"] is False
    prefs = client.put("/settings/desktop", headers=auth,
                       json={"autostart": True, "bogus": 1}).json()
    assert prefs["autostart"] is True and "bogus" not in prefs
    pubs = {p["id"]: p for p in client.get("/social/publishers", headers=auth).json()}
    assert set(pubs) == {"youtube", "facebook_page", "mock_youtube", "mock_facebook"}
    assert pubs["youtube"]["constraints"]["max_title_len"] == 100
    assert pubs["mock_youtube"]["mock"] is True
    assert "category_id" in pubs["youtube"]["metadata_schema"]["properties"]


def test_suggest_metadata_with_mock_llm(client: TestClient, auth):
    video = make_approved_video(client, thumbnail="none")
    r = client.post(f"/videos/{video['id']}/suggest-metadata", headers=auth,
                    json={"platform": "youtube"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["titles"][0].startswith("Quang hợp ở thực vật")
    assert all(h.startswith("#") for h in s["hashtags"])
    assert "#QuangHopOThucVat" in s["hashtags"]
    assert s["description"]


def test_publish_meta_merge(client: TestClient, auth):
    video = make_approved_video(client, thumbnail="none")
    m = client.put(f"/videos/{video['id']}/publish-meta", headers=auth, json={
        "facebook": {"title": "Tiêu đề Facebook", "options": {"format": "reels"}}}).json()
    assert m["facebook"]["title"] == "Tiêu đề Facebook"
    assert m["common"]["title"] == "Quang hợp ở thực vật"
    m = client.put(f"/videos/{video['id']}/publish-meta", headers=auth, json={
        "facebook": {"options": {}}}).json()
    assert m["facebook"]["options"] == {} and m["facebook"]["title"] == "Tiêu đề Facebook"


@requires_ffmpeg
def test_auto_thumbnail_from_real_render(client: TestClient, auth):
    from .test_review import make_video, render_video

    sb = make_video(client, auth)
    render = render_video(client, auth, sb["id"])
    r = client.post(f"/videos/{sb['id']}/thumbnail", headers=auth,
                    json={"render_id": render["id"], "frame_s": 1.0, "text": "Tiêu đề ảnh"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["thumbnail"]["frame_s"] == 1.0 and out["aspect"] == "9:16"
    img = client.get(out["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(img.content)) as im:
        assert im.size == (720, 1280)
    assert len(img.content) < 2 * 1024 * 1024
    up = client.post(f"/videos/{sb['id']}/thumbnail/upload", headers=auth,
                     files={"file": ("ảnh bìa.png", _png(), "image/png")})
    assert up.status_code == 200 and up.json()["thumbnail"]["mode"] == "custom"
    bad = client.post(f"/videos/{sb['id']}/thumbnail/upload", headers=auth,
                      files={"file": ("x.gif", b"GIF89a", "image/gif")})
    assert bad.status_code == 415


def _png() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (800, 600), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("platform", ["youtube", "facebook"])
def test_disconnect_blocked_while_pending(client: TestClient, auth, platform):
    video = make_approved_video(client, thumbnail="none")
    aid = _mock_account(client, auth, platform)
    client.post(f"/videos/{video['id']}/publications", headers=auth, json={"items": [
        {"account_id": aid, "schedule": {
            "mode": "local", "at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}}]})
    assert client.delete(f"/social/accounts/{aid}", headers=auth).status_code == 409
