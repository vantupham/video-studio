"""End-to-end pipeline through the HTTP API: markdown → IR → storyboard (mock LLM) →
edit / regenerate a scene → render (mock TTS) → outputs."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from videostudio.media.ffmpeg import detect_ffmpeg, probe

from .conftest import FIXTURES
from .test_api import wait_job

HAVE_FFMPEG = detect_ffmpeg().found
requires_ffmpeg = pytest.mark.skipif(
    not HAVE_FFMPEG and os.environ.get("VIDEOSTUDIO_REQUIRE_FFMPEG") != "1",
    reason="ffmpeg not installed")


def _instance(client, auth, type_id):
    insts = client.get("/providers", headers=auth).json()["instances"]
    return next(i for i in insts if i["type"] == type_id)


def _project_with_document(client: TestClient, auth) -> str:
    pid = client.post("/projects", headers=auth, json={"name": "Dự án video"}).json()["id"]
    path = FIXTURES / "bài học tiếng việt.md"
    with path.open("rb") as fh:
        r = client.post(f"/projects/{pid}/sources", headers=auth,
                        files={"file": (path.name, fh, "text/markdown")})
    assert r.status_code == 201, r.text
    job = client.post(f"/projects/{pid}/ingest", headers=auth, json={}).json()
    assert wait_job(client, auth, job["id"])["status"] == "succeeded"
    return pid


def _make_storyboard(client, auth, pid, **settings) -> dict:
    job = client.post(f"/projects/{pid}/storyboards", headers=auth,
                      json={"settings": settings}).json()
    done = wait_job(client, auth, job["id"], timeout=30)
    assert done["status"] == "succeeded", done
    sid = done["result"]["storyboard_id"]
    return client.get(f"/storyboards/{sid}", headers=auth).json()


def test_styles_and_defaults_endpoints(client: TestClient, auth):
    styles = client.get("/styles", headers=auth).json()
    assert {s["id"] for s in styles} >= {"explainer_16x9", "short_9x16"}
    detail = client.get("/styles/short_9x16", headers=auth).json()
    assert detail["prompts"]["script"]["origin"] == "builtin"
    assert "9:16" in detail["prompts"]["script"]["text"]
    d = client.get("/generation/defaults", headers=auth).json()
    assert [s["id"] for s in d["render_steps"]] == ["tts", "media", "render", "subtitles",
                                                    "encode"]
    assert client.get("/storyboard/schema", headers=auth).json()["title"] == "Storyboard"


def test_project_settings_override_and_validation(client: TestClient, auth):
    pid = client.post("/projects", headers=auth, json={"name": "p"}).json()["id"]
    mock_tts = _instance(client, auth, "mock_tts")
    r = client.put(f"/projects/{pid}/settings", headers=auth, json={
        "style": "short_9x16", "tts": {"instance_id": mock_tts["id"], "voice": "beep-high"}})
    assert r.status_code == 200
    got = client.get(f"/projects/{pid}/settings", headers=auth).json()
    assert got["effective"]["style"] == "short_9x16"
    assert got["effective"]["tts"]["voice"] == "beep-high"
    assert client.put(f"/projects/{pid}/settings", headers=auth,
                      json={"target_duration_s": 1}).status_code == 422


def test_script_job_errors_without_document(client: TestClient, auth):
    pid = client.post("/projects", headers=auth, json={"name": "trống"}).json()["id"]
    job = client.post(f"/projects/{pid}/storyboards", headers=auth, json={}).json()
    done = wait_job(client, auth, job["id"])
    assert done["status"] == "failed" and "tài liệu" in done["error"]


def test_storyboard_create_edit_regenerate(client: TestClient, auth):
    pid = _project_with_document(client, auth)
    sb = _make_storyboard(client, auth, pid, style="explainer_16x9", target_duration_s=30,
                          max_scenes=4)
    data = sb["data"]
    assert data["style"] == "explainer_16x9" and data["aspect"] == "16:9"
    assert data["scenes"][0]["visual"]["type"] == "title_card"
    assert sb["settings"]["llm"]["instance_id"] == _instance(client, auth, "mock_llm")["id"]
    assert sb["settings"]["tts"]["instance_id"]  # default resolved and frozen

    # edit: change narration + visual of scene 2, set a fixed duration
    data["scenes"][1]["narration"] = "Lời thoại đã sửa bằng tay."
    data["scenes"][1]["visual"] = {"type": "solid", "title": "Sửa", "color": "#123456"}
    data["scenes"][1]["duration"] = 3
    r = client.put(f"/storyboards/{sb['id']}", headers=auth, json={"data": data})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["scenes"][1]["visual"]["color"] == "#123456"
    bad = dict(data, scenes=[])
    assert client.put(f"/storyboards/{sb['id']}", headers=auth,
                      json={"data": bad}).status_code == 422

    job = client.post(f"/storyboards/{sb['id']}/scenes/s1/regenerate", headers=auth,
                      json={"instruction": "viết ngắn hơn"}).json()
    assert wait_job(client, auth, job["id"])["status"] == "succeeded"
    after = client.get(f"/storyboards/{sb['id']}", headers=auth).json()["data"]
    assert after["scenes"][1] == r.json()["data"]["scenes"][1]  # other scenes untouched
    assert len(after["scenes"][0]["narration"]) <= len(data["scenes"][0]["narration"])
    assert client.post(f"/storyboards/{sb['id']}/scenes/nope/regenerate", headers=auth,
                       json={}).status_code == 404

    listed = client.get(f"/projects/{pid}/storyboards", headers=auth).json()
    assert listed[0]["id"] == sb["id"] and listed[0]["scenes"] == len(after["scenes"])


def test_use_as_is_and_per_storyboard_llm_override(client: TestClient, auth):
    pid = _project_with_document(client, auth)
    sb = _make_storyboard(client, auth, pid, use_as_is=True, style="short_9x16")
    assert sb["data"]["aspect"] == "9:16"
    assert sb["settings"]["use_as_is"] is True


@requires_ffmpeg
def test_full_render_with_mock_tts_and_scene_cache(client: TestClient, auth, settings):
    pid = _project_with_document(client, auth)
    mock_tts = _instance(client, auth, "mock_tts")
    client.patch(f"/providers/{mock_tts['id']}", headers=auth,
                 json={"config": {"words_per_second": 8}})
    sb = _make_storyboard(client, auth, pid, style="short_9x16", target_duration_s=15,
                          max_scenes=3, tts={"instance_id": mock_tts["id"]})
    assert sb["settings"]["tts"]["instance_id"] == mock_tts["id"]

    r = client.post(f"/storyboards/{sb['id']}/render", headers=auth, json={"preview": True})
    assert r.status_code == 202
    job = wait_job(client, auth, r.json()["job"]["id"], timeout=120)
    assert job["status"] == "succeeded", job
    assert job["result"]["tts_cache_hits"] == 0
    render = client.get(f"/renders/{r.json()['render_id']}", headers=auth).json()
    assert render["status"] == "done" and render["size_bytes"] > 0
    assert set(render["outputs"]) >= {"video", "srt", "vtt", "ass", "thumbnail"}
    assert render["outputs"]["video"].startswith(f"projects/{pid}/outputs/")
    assert render["settings"]["burned_subtitles"] is True
    video = settings.data_dir / render["outputs"]["video"]
    info = probe(video)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (540, 960)
    assert float(info["format"]["duration"]) == pytest.approx(render["duration_s"], abs=0.1)
    # media URLs work without the bearer token (media key) and support ranges
    resp = client.get(render["urls"]["video"], headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206 and len(resp.content) == 100
    assert client.get(render["urls"]["srt"]).status_code == 200

    # narration assets are recorded per scene
    sb2 = client.get(f"/storyboards/{sb['id']}", headers=auth).json()
    assert set(sb2["assets"]) == {s["id"] for s in sb2["data"]["scenes"] if s["narration"]}
    assert client.get(next(iter(sb2["assets"].values()))["audio_url"]).status_code == 200

    # edit one scene → only that scene is synthesised again
    data = sb2["data"]
    data["scenes"][-1]["narration"] += " Thêm một câu."
    client.put(f"/storyboards/{sb['id']}", headers=auth, json={"data": data})
    r2 = client.post(f"/storyboards/{sb['id']}/render", headers=auth, json={"preview": True})
    job2 = wait_job(client, auth, r2.json()["job"]["id"], timeout=120)
    assert job2["status"] == "succeeded", job2
    narrated = sum(1 for s in data["scenes"] if s["narration"])
    assert job2["result"]["tts_cache_hits"] == narrated - 1
    assert len(client.get(f"/projects/{pid}/renders", headers=auth).json()) == 2

    # storyboard images are exposed as media URLs for the editor
    img = client.post("/assets", headers=auth, data={"kind": "image", "project_id": pid},
                      files={"file": ("anh.png", _png(), "image/png")}).json()
    data["scenes"][0]["visual"] = {"type": "image", "image": img["rel_path"]}
    sb3 = client.put(f"/storyboards/{sb['id']}", headers=auth, json={"data": data}).json()
    first = data["scenes"][0]["id"]
    assert list(sb3["image_urls"]) == [first]
    assert client.get(sb3["image_urls"][first]).status_code == 200

    # deleting a render removes its files
    assert client.delete(f"/renders/{r.json()['render_id']}", headers=auth).status_code == 204
    assert not video.exists()


@requires_ffmpeg
def test_render_failure_is_recorded(client: TestClient, auth):
    pid = _project_with_document(client, auth)
    # an instance whose provider will fail at synthesis time
    bad = client.post("/providers", headers=auth, json={
        "type": "openai_tts", "config": {"api_key": "sk-bad-000000", "base_url":
                                         "http://127.0.0.1:9/v1", "timeout_s": 5}}).json()
    sb = _make_storyboard(client, auth, pid, max_scenes=2, tts={"instance_id": bad["id"]})
    r = client.post(f"/storyboards/{sb['id']}/render", headers=auth, json={"preview": True})
    job = wait_job(client, auth, r.json()["job"]["id"], timeout=60)
    assert job["status"] == "failed" and "OpenAI TTS" in job["error"]
    assert "sk-bad" not in job["error"]
    render = client.get(f"/renders/{r.json()['render_id']}", headers=auth).json()
    assert render["status"] == "failed" and render["error"]


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 18), (20, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def test_assets_upload_and_branding(client: TestClient, auth):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (64, 32), (255, 0, 0, 128)).save(buf, "PNG")
    r = client.post("/assets", headers=auth, data={"kind": "logo"},
                    files={"file": ("logo kênh.png", buf.getvalue(), "image/png")})
    assert r.status_code == 201, r.text
    asset = r.json()
    assert client.get(asset["url"]).status_code == 200
    assert client.post("/assets", headers=auth, data={"kind": "audio"},
                       files={"file": ("x.exe", b"MZ", "application/octet-stream")}
                       ).status_code == 415
    b = client.put("/settings/branding", headers=auth, json={
        "enabled": True, "logo_asset_id": asset["id"], "intro_text": "Kênh Học Vui",
        "unknown": 1}).json()
    assert b["enabled"] and b["logo_url"] and "unknown" not in b
    assert client.get("/settings/branding", headers=auth).json()["intro_text"] == "Kênh Học Vui"
    assert client.delete(f"/assets/{asset['id']}", headers=auth).status_code == 204
