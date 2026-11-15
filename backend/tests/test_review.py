"""Review workflow: lifecycle state machine, history, version compare, library listing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from videostudio.review import lifecycle
from videostudio.review.compare import diff_snapshots

from .test_api import wait_job
from .test_pipeline_api import _instance, _make_storyboard, _project_with_document, requires_ffmpeg


def make_video(client: TestClient, auth, **settings) -> dict:
    pid = _project_with_document(client, auth)
    mock_tts = _instance(client, auth, "mock_tts")
    settings = {"style": "short_9x16", "target_duration_s": 15, "max_scenes": 3,
                "tts": {"instance_id": mock_tts["id"]}, **settings}
    return _make_storyboard(client, auth, pid, **settings)


def render_video(client: TestClient, auth, video_id: str, **body) -> dict:
    r = client.post(f"/storyboards/{video_id}/render", headers=auth, json=body)
    assert r.status_code == 202, r.text
    job = wait_job(client, auth, r.json()["job"]["id"], timeout=180)
    assert job["status"] == "succeeded", job
    return client.get(f"/renders/{r.json()['render_id']}", headers=auth).json()


def act(client, auth, vid, action, note="", expect=200) -> dict:
    r = client.post(f"/videos/{vid}/actions", headers=auth, json={"action": action,
                                                                  "note": note})
    assert r.status_code == expect, r.text
    return r.json()


def test_aggregate_publication_status():
    agg = lifecycle.aggregate_publication_status
    assert agg([]) is None and agg(["cancelled"]) is None
    assert agg(["published", "uploading"]) == "publishing"
    assert agg(["published", "failed"]) == "failed"
    assert agg(["published", "scheduled_remote"]) == "scheduled"
    assert agg(["missed"]) == "scheduled"
    assert agg(["published", "cancelled"]) == "published"


def test_content_hash_ignores_aspect_only():
    data = {"title": "A", "aspect": "16:9", "scenes": [{"id": "s1", "narration": "x"}]}
    h = lifecycle.content_hash(data, {"aspect": "16:9", "voice": "a"})
    assert h == lifecycle.content_hash({**data, "aspect": "9:16"}, {"aspect": "9:16",
                                                                    "voice": "a"})
    assert h != lifecycle.content_hash({**data, "title": "B"}, {"voice": "a"})
    assert h != lifecycle.content_hash(data, {"voice": "b"})


def test_diff_snapshots():
    a = {"title": "T", "scenes": [{"id": "s1", "narration": "một"},
                                  {"id": "s2", "narration": "hai"},
                                  {"id": "s3", "narration": "ba"}]}
    b = {"title": "T2", "scenes": [{"id": "s2", "narration": "hai"},
                                   {"id": "s1", "narration": "MỘT"},
                                   {"id": "s4", "narration": "bốn"}]}
    d = diff_snapshots(a, b)
    by = {s["scene_id"]: s for s in d["scenes"]}
    assert by["s1"]["change"] == "changed" and by["s1"]["fields"] == ["narration"]
    assert by["s2"]["change"] == "same"
    assert by["s3"]["change"] == "removed" and by["s4"]["change"] == "added"
    assert d["reordered"] and d["title_changed"]
    assert d["summary"] == {"added": 1, "removed": 1, "changed": 1, "same": 1}


def test_actions_rejected_in_wrong_state(client: TestClient, auth):
    sb = make_video(client, auth)
    v = client.get(f"/videos/{sb['id']}", headers=auth).json()
    assert v["status"] == "draft" and v["status_label"] == "Bản nháp"
    assert v["actions"] == []
    assert "trạng thái" in act(client, auth, sb["id"], "approve", expect=409)["detail"]
    assert act(client, auth, sb["id"], "bogus", expect=422)
    assert client.get("/videos/nope", headers=auth).status_code == 404


@requires_ffmpeg
def test_review_cycle_with_partial_rerender(client: TestClient, auth):
    sb = make_video(client, auth)
    vid = sb["id"]
    r1 = render_video(client, auth, vid)
    v = client.get(f"/videos/{vid}", headers=auth).json()
    assert v["status"] == "rendered"
    assert [e["action"] for e in v["events"]][:2] == ["render_done", "render_started"]
    main = [r for r in v["renders"] if r["kind"] == "main"]
    assert main[0]["version"] == 1 and main[0]["current"]
    timeline = main[0]["timeline"]
    assert [t["scene_id"] for t in timeline] == [s["id"] for s in sb["data"]["scenes"]]
    assert timeline[0]["start"] == 0 and timeline[-1]["end"] == pytest.approx(
        r1["duration_s"], abs=0.1)

    v = act(client, auth, vid, "submit")
    assert v["status"] == "in_review"
    assert {a["id"] for a in v["actions"]} == {"approve", "request_changes"}
    assert act(client, auth, vid, "request_changes", expect=422)["detail"].startswith("Hãy")
    v = act(client, auth, vid, "request_changes", "Cảnh 2 đọc chậm lại, sửa chữ sai")
    assert v["status"] == "changes_requested"
    assert v["events"][0]["note"] == "Cảnh 2 đọc chậm lại, sửa chữ sai"
    assert v["events"][0]["actor"] == "user"

    # edit one scene → the latest render is outdated → re-render only that scene
    data = sb["data"]
    data["scenes"][1]["visual"]["title"] = "Tiêu đề đã sửa"
    assert client.put(f"/storyboards/{vid}", headers=auth, json={"data": data}).status_code \
        == 200
    v = client.get(f"/videos/{vid}", headers=auth).json()
    assert v["outdated"] and not v["renders"][0]["current"]
    r = client.post(f"/storyboards/{vid}/render", headers=auth, json={}).json()
    job = wait_job(client, auth, r["job"]["id"], timeout=180)
    assert job["status"] == "succeeded"
    rendered = [entry for entry in job["logs"] if "dựng hình" in entry["message"]]
    assert len(rendered) == 1  # only the edited scene's segment was re-encoded

    v = act(client, auth, vid, "submit")
    v = act(client, auth, vid, "approve", "OK")
    assert v["status"] == "approved"
    assert v["approved_render_id"] == r["render_id"]
    assert v["approved_hash"] == v["content_hash"]
    mains = [x for x in v["renders"] if x["kind"] == "main"]
    assert [x["version"] for x in mains] == [2, 1]

    diff = client.get(f"/videos/{vid}/compare", headers=auth,
                      params={"a": r1["id"], "b": r["render_id"]}).json()
    changed = [s for s in diff["scenes"] if s["change"] == "changed"]
    assert len(changed) == 1 and changed[0]["fields"] == ["visual"]
    assert changed[0]["after"]["visual"]["title"] == "Tiêu đề đã sửa"

    # approved render cannot be deleted; history keeps every step with timestamps
    assert client.delete(f"/renders/{r['render_id']}", headers=auth).status_code == 409
    actions = [e["action"] for e in reversed(v["events"])]
    assert actions == ["render_started", "render_done", "submit", "request_changes",
                       "render_started", "render_done", "submit", "approve"]

    # a 16:9 variant of approved content keeps the approval
    variant = render_video(client, auth, vid, aspect="16:9", preview=True)
    assert variant["aspect"] == "16:9"
    v = client.get(f"/videos/{vid}", headers=auth).json()
    assert v["status"] == "approved"
    var = next(x for x in v["renders"] if x["id"] == variant["id"])
    assert var["kind"] == "preview"
    variant = render_video(client, auth, vid, aspect="16:9")
    v = client.get(f"/videos/{vid}", headers=auth).json()
    var = next(x for x in v["renders"] if x["id"] == variant["id"])
    assert var["kind"] == "variant" and var["approved_content"]
    assert (variant["settings"]["width"], variant["settings"]["height"]) == (1920, 1080)
    assert v["status"] == "approved" and v["events"][0]["action"] == "variant_rendered"

    # editing + re-render after approval drops back into the review cycle
    v = act(client, auth, vid, "reopen")
    assert v["status"] == "in_review" and v["approved_render_id"] is None

    lib = client.get("/videos", headers=auth).json()
    assert lib["counts"]["in_review"] == 1
    item = lib["items"][0]
    assert item["id"] == vid and item["project_name"] == "Dự án video"
    assert item["thumbnail_url"].startswith("/media/")
    assert client.get("/videos", headers=auth, params={"status": "approved"}).json()[
        "items"] == []
    assert len(client.get("/videos", headers=auth,
                          params={"q": sb["title"][:5]}).json()["items"]) == 1


@requires_ffmpeg
def test_render_failure_restores_previous_status(client: TestClient, auth):
    sb = make_video(client, auth)
    render_video(client, auth, sb["id"])
    act(client, auth, sb["id"], "submit")
    bad = client.post("/providers", headers=auth, json={
        "type": "openai_tts", "config": {"api_key": "sk-bad-000000",
                                         "base_url": "http://127.0.0.1:9/v1"}}).json()
    data = sb["data"]
    data["scenes"][0]["narration"] = "Câu mới cần đọc lại."
    client.put(f"/storyboards/{sb['id']}", headers=auth,
               json={"data": data, "settings": {"tts": {"instance_id": bad["id"]}}})
    r = client.post(f"/storyboards/{sb['id']}/render", headers=auth, json={}).json()
    assert wait_job(client, auth, r["job"]["id"], timeout=60)["status"] == "failed"
    v = client.get(f"/videos/{sb['id']}", headers=auth).json()
    assert v["status"] == "in_review"
    assert v["events"][0]["action"] == "render_failed" and v["events"][0]["note"]


def test_stale_generating_is_reset_on_startup(settings, session_factory):
    from videostudio.db import Project, Storyboard

    with session_factory.begin() as s:
        p = Project(name="p")
        s.add(p)
        s.flush()
        row = Storyboard(project_id=p.id, title="t", data={"scenes": []}, status="generating")
        s.add(row)
        s.flush()
        assert lifecycle.reset_stale_generating(s) == 1
        assert row.status == "draft"


def test_render_locked_while_scheduled(client: TestClient, auth, session_factory):
    from videostudio.db import Storyboard

    sb = make_video(client, auth)
    with session_factory.begin() as s:
        s.get(Storyboard, sb["id"]).status = "scheduled"
    r = client.post(f"/storyboards/{sb['id']}/render", headers=auth, json={})
    assert r.status_code == 409 and "lịch" in r.json()["detail"]
    # variants are fine (e.g. re-render in the platform's aspect)
    r = client.post(f"/storyboards/{sb['id']}/render", headers=auth,
                    json={"aspect": "16:9", "preview": True})
    assert r.status_code == 202
    assert client.post(f"/storyboards/{sb['id']}/render", headers=auth,
                       json={"aspect": "4:3"}).status_code == 422
