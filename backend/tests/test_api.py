from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .conftest import FIXTURES, TOKEN


def wait_job(client: TestClient, auth: dict, job_id: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{job_id}", headers=auth).json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/health"), ("get", "/projects"), ("post", "/projects"), ("get", "/adapters"),
     ("get", "/jobs"), ("get", "/system/info"), ("post", "/jobs/demo")],
)
def test_requires_token(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert getattr(client, method)(path, headers=bad).status_code == 401


def test_health_with_token(client: TestClient, auth: dict) -> None:
    r = client.get("/health", headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "ok"
    r = client.get("/health", headers={"X-VideoStudio-Token": TOKEN})
    assert r.status_code == 200


def test_cors_preflight_without_token(client: TestClient) -> None:
    r = client.options(
        "/projects",
        headers={"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "authorization"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_projects_crud(client: TestClient, auth: dict, settings) -> None:
    r = client.post("/projects", json={"name": "Dự án đầu tiên", "description": "Mô tả"},
                    headers=auth)
    assert r.status_code == 201
    project = r.json()
    pid = project["id"]
    assert (settings.data_dir / "projects" / pid).is_dir()

    listing = client.get("/projects", headers=auth).json()
    assert [p["name"] for p in listing] == ["Dự án đầu tiên"]

    r = client.patch(f"/projects/{pid}", json={"name": "Đổi tên"}, headers=auth)
    assert r.json()["name"] == "Đổi tên"
    assert client.get(f"/projects/{pid}", headers=auth).json()["description"] == "Mô tả"

    assert client.delete(f"/projects/{pid}", headers=auth).status_code == 204
    assert client.get(f"/projects/{pid}", headers=auth).status_code == 404
    assert not (settings.data_dir / "projects" / pid).exists()
    assert client.post("/projects", json={"name": ""}, headers=auth).status_code == 422


def test_adapters_listing(client: TestClient, auth: dict) -> None:
    adapters = {a["id"]: a for a in client.get("/adapters", headers=auth).json()}
    assert {"script", "markdown"} <= set(adapters)
    md = adapters["markdown"]
    assert md["config_schema"]["type"] == "object"
    assert md["config_schema"]["properties"]["section_level"]["default"] == 2
    assert ".md" in md["file_extensions"]


def test_ir_schema_endpoint(client: TestClient, auth: dict) -> None:
    schema = client.get("/ir/schema", headers=auth).json()
    assert schema["title"] == "ContentIR"


def _create_project(client: TestClient, auth: dict) -> str:
    return client.post("/projects", json={"name": "Video hướng dẫn"}, headers=auth).json()["id"]


def test_source_validation(client: TestClient, auth: dict) -> None:
    pid = _create_project(client, auth)
    assert client.post(f"/projects/{pid}/sources", headers=auth).status_code == 422
    r = client.post(f"/projects/{pid}/sources", data={"text": "abc", "adapter_id": "nope"},
                    headers=auth)
    assert r.status_code == 422
    r = client.post(f"/projects/{pid}/sources",
                    data={"text": "abc", "adapter_id": "script", "config": '{"bogus": 1}'},
                    headers=auth)
    assert r.status_code == 422
    assert client.post(f"/projects/{pid}/ingest", headers=auth).status_code == 422
    assert client.post("/projects/missing/sources", data={"text": "a"},
                       headers=auth).status_code == 404


def test_end_to_end_ingest(client: TestClient, auth: dict, settings) -> None:
    pid = _create_project(client, auth)

    script = "Cảnh 1: Mở đầu\nXin chào các bạn.\n\nCảnh 2: Kết\nTạm biệt và hẹn gặp lại!"
    r = client.post(
        f"/projects/{pid}/sources",
        data={"text": script, "name": "Kịch bản dán", "adapter_id": "script",
              "config": json.dumps({"language": "vi", "split_mode": "blank_line"})},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    text_source = r.json()
    assert text_source["kind"] == "text" and text_source["filename"] == "Kịch bản dán.txt"

    md_path = FIXTURES / "bài học tiếng việt.md"
    with md_path.open("rb") as fh:
        r = client.post(f"/projects/{pid}/sources",
                        files={"file": (md_path.name, fh, "text/markdown")}, headers=auth)
    assert r.status_code == 201, r.text
    md_source = r.json()
    assert md_source["adapter_id"] == "markdown"  # auto-detected from the extension
    assert md_source["filename"] == "bài học tiếng việt.md"

    content = client.get(f"/projects/{pid}/sources/{text_source['id']}/content", headers=auth)
    assert content.text == script

    r = client.post(f"/projects/{pid}/ingest", json={}, headers=auth)
    assert r.status_code == 202
    job = wait_job(client, auth, r.json()["id"])
    assert job["status"] == "succeeded", job
    assert job["project_id"] == pid
    assert len(job["result"]["documents"]) == 2

    docs = client.get(f"/projects/{pid}/documents", headers=auth).json()
    assert {d["adapter_id"] for d in docs} == {"script", "markdown"}
    md_doc = next(d for d in docs if d["adapter_id"] == "markdown")
    assert md_doc["title"] == "Hướng dẫn làm video tự động"
    assert md_doc["stats"]["table"] == 1

    full = client.get(f"/projects/{pid}/documents/{md_doc['id']}", headers=auth).json()
    assert full["data"]["sections"][1]["title"] == "Phần 1: Chuẩn bị"
    ir_file = settings.data_dir / full["rel_path"]
    assert ir_file.is_file()
    on_disk = json.loads(ir_file.read_text(encoding="utf-8"))
    assert on_disk == full["data"]
    assert "Hướng dẫn" in ir_file.read_text(encoding="utf-8")  # stored unescaped (UTF-8)

    detail = client.get(f"/projects/{pid}", headers=auth).json()
    assert detail["source_count"] == 2 and detail["document_count"] == 2

    # Ingest a single source only.
    r = client.post(f"/projects/{pid}/ingest", json={"source_ids": [text_source["id"]]},
                    headers=auth)
    job = wait_job(client, auth, r.json()["id"])
    assert [d["source_id"] for d in job["result"]["documents"]] == [text_source["id"]]
    r = client.post(f"/projects/{pid}/ingest", json={"source_ids": ["other"]}, headers=auth)
    assert r.status_code == 422

    assert client.delete(f"/projects/{pid}/sources/{md_source['id']}",
                         headers=auth).status_code == 204
    assert len(client.get(f"/projects/{pid}/sources", headers=auth).json()) == 1


def test_jobs_endpoints(client: TestClient, auth: dict) -> None:
    r = client.post("/jobs/demo", json={"seconds": 0.2, "steps": 2}, headers=auth)
    assert r.status_code == 202
    job = wait_job(client, auth, r.json()["id"])
    assert job["status"] == "succeeded" and job["progress"] == 100

    long_job = client.post("/jobs/demo", json={"seconds": 30, "steps": 300}, headers=auth).json()
    r = client.post(f"/jobs/{long_job['id']}/cancel", headers=auth)
    assert r.status_code == 200
    assert wait_job(client, auth, long_job["id"])["status"] == "cancelled"
    assert client.post(f"/jobs/{long_job['id']}/cancel", headers=auth).status_code == 409
    assert client.get("/jobs/nope", headers=auth).status_code == 404

    listing = client.get("/jobs", params={"status": "cancelled"}, headers=auth).json()
    assert [j["id"] for j in listing] == [long_job["id"]]
    assert client.get("/jobs", params={"status": "weird"}, headers=auth).status_code == 422


def test_websocket_requires_token(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/jobs?token=wrong") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_websocket_streams_progress(client: TestClient, auth: dict) -> None:
    with client.websocket_connect(f"/ws/jobs?token={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "hello"
        job_id = client.post("/jobs/demo", json={"seconds": 0.3, "steps": 3},
                             headers=auth).json()["id"]
        seen = []
        while True:
            event = ws.receive_json()
            if event["type"] == "job" and event["job"]["id"] == job_id:
                seen.append((event["job"]["status"], event["job"]["progress"]))
                if event["job"]["status"] == "succeeded":
                    break
    statuses = [s for s, _ in seen]
    assert statuses[0] == "queued" and "running" in statuses
    assert (("running", 33) in seen) or (("running", 34) in seen)


def test_system_info_and_secret_selftest(client: TestClient, auth: dict, settings) -> None:
    info = client.get("/system/info", headers=auth).json()
    assert info["data_dir"] == str(settings.data_dir)
    assert "found" in info["ffmpeg"]
    assert set(info["job_types"]) >= {"demo", "ingest"}

    assert client.get("/secrets/selftest", headers=auth).json()["value"] is None
    r = client.post("/secrets/selftest", json={"value": "mật khẩu bí mật 🔐"}, headers=auth)
    body = r.json()
    assert body["ok"] is True and body["read_back"] == "mật khẩu bí mật 🔐"
    assert client.get("/secrets/selftest", headers=auth).json()["value"] == "mật khẩu bí mật 🔐"
    raw = b"".join(p.read_bytes() for p in (settings.data_dir / "secrets").glob("*.secret"))
    assert "mật khẩu".encode() not in raw
