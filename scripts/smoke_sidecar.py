"""Smoke-test the frozen backend sidecar exactly like the Tauri shell launches it.

    python scripts/smoke_sidecar.py <path to videostudio-backend[.exe]> [--render]
        [--documents <fixtures dir>] [--publish]

Starts the executable with a random token and ``--port 0``, reads the port from
stdout, then checks auth, migrations (project CRUD), an ingest job with Vietnamese
text and the secret store. ``--render`` additionally generates a storyboard with the
demo LLM and renders both built-in styles with the offline mock voice (needs FFmpeg:
``VIDEOSTUDIO_FFMPEG_DIR``, ``<install>/ffmpeg`` or PATH).

``--documents DIR`` ingests the DOCX, text PDF and scanned PDF made by
``backend/tests/fixtures/make_fixtures.py`` (the scan goes through the default OCR
provider, Tesseract) and renders the DOCX. ``--publish`` then approves that video and
publishes it to a mock YouTube account. Uses only the standard library.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

TIMEOUT_S = 120
RENDER_TIMEOUT_S = 600


def request(base: str, method: str, path: str, token: str | None, body=None, form=None,
            files=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if form is not None or files is not None:
        boundary = uuid.uuid4().hex
        parts: list[bytes] = []
        for key, value in (form or {}).items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                .encode()
            )
        for key, (filename, content) in (files or {}).items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
                f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
                .encode() + content + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            if "json" not in (res.headers.get("content-type") or ""):
                return res.status, raw
            return res.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        return err.code, None


def wait_job(base: str, token: str, job: dict, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while job["status"] not in ("succeeded", "failed", "cancelled"):
        if time.monotonic() > deadline:
            raise RuntimeError(f"job {job['type']} timed out at {job['progress']}%")
        time.sleep(0.3)
        job = request(base, "GET", f"/jobs/{job['id']}", token)[1]
    return job


def smoke_render(base: str, token: str, pid: str, doc_id: str) -> None:
    status, providers = request(base, "GET", "/providers", token)
    assert status == 200, status
    types = {i["type"]: i for i in providers["instances"]}
    assert {"mock_llm", "edge_tts", "mock_tts", "proportional"} <= set(types), types.keys()
    assert providers["defaults"]["llm"] == types["mock_llm"]["id"]
    status, _ = request(base, "POST", f"/providers/{types['mock_tts']['id']}/default", token)
    assert status == 200, status
    for style in ("explainer_16x9", "short_9x16"):
        started = time.monotonic()
        status, job = request(base, "POST", f"/projects/{pid}/storyboards", token, body={
            "document_id": doc_id,
            "settings": {"style": style, "target_duration_s": 10, "max_scenes": 3}})
        assert status == 202, status
        job = wait_job(base, token, job, TIMEOUT_S)
        assert job["status"] == "succeeded", job
        sid = job["result"]["storyboard_id"]
        status, res = request(base, "POST", f"/storyboards/{sid}/render", token,
                              body={"preview": True})
        assert status == 202, status
        job = wait_job(base, token, res["job"], RENDER_TIMEOUT_S)
        assert job["status"] == "succeeded", job.get("error") or job
        status, render = request(base, "GET", f"/renders/{res['render_id']}", token)
        assert status == 200 and render["status"] == "done", render
        assert {"video", "srt", "vtt", "ass", "thumbnail"} <= set(render["outputs"]), render
        status, head = request(base, "GET", render["urls"]["video"], None)
        assert status == 200 and len(head) == render["size_bytes"] > 10_000, status
        status, srt = request(base, "GET", render["urls"]["srt"], None)
        assert status == 200 and "-->" in srt.decode("utf-8-sig"), status
        print(f"render ok: {style} {render['settings']['width']}x{render['settings']['height']} "
              f"{render['duration_s']:.1f}s {render['size_bytes'] / 1e6:.2f} MB "
              f"encoder={render['settings']['encoder']} in {time.monotonic() - started:.1f}s")


# Expected text per fixture: Vietnamese words with diacritics must survive extraction.
DOCUMENT_CHECKS = {
    "tài liệu mẫu.docx": ("docx", "Hướng dẫn trồng cà phê", ["Tây Nguyên"]),
    "tài liệu mẫu.pdf": ("pdf", "Du lịch Việt Nam", ["Vịnh Hạ Long", "Sửng Sốt"]),
    # Words Tesseract reads reliably in both Times New Roman (Windows) and Liberation Serif.
    "bản scan.pdf": ("pdf", None, ["Phở", "Bánh mì Sài Gòn", "thế giới"]),
}


def _ir_text(ir: dict) -> str:
    out: list[str] = [ir.get("title") or ""]

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("text", "title") and isinstance(value, str):
                    out.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(ir.get("sections", []))
    return "\n".join(out)


def smoke_documents(base: str, token: str, pid: str, fixtures: Path) -> str:
    """Ingest DOCX / text PDF / scanned PDF; returns the DOCX document id."""
    docx_id = ""
    for filename, (adapter, title, words) in DOCUMENT_CHECKS.items():
        started = time.monotonic()
        status, src = request(base, "POST", f"/projects/{pid}/sources", token,
                              files={"file": (filename, (fixtures / filename).read_bytes())})
        assert status == 201, (filename, status)
        assert src["adapter_id"] == adapter, src
        status, job = request(base, "POST", f"/projects/{pid}/ingest", token,
                              body={"source_ids": [src["id"]]})
        assert status == 202, status
        job = wait_job(base, token, job, RENDER_TIMEOUT_S)
        assert job["status"] == "succeeded", job.get("error") or job
        doc = job["result"]["documents"][0]
        status, full = request(base, "GET", f"/projects/{pid}/documents/{doc['id']}", token)
        assert status == 200, status
        text = _ir_text(full["data"])
        if title:
            assert full["data"]["title"] == title, full["data"]["title"]
        missing = [w for w in words if w not in text]
        assert not missing, f"{filename}: missing {missing} in extracted text: {text[:400]!r}"
        meta = full["data"].get("metadata") or {}
        if filename == "bản scan.pdf":
            assert meta.get("ocr_engine") == "tesseract", meta
        print(f"document ok: {filename} via {adapter}"
              f"{' + OCR ' + str(meta.get('ocr_engine')) if meta.get('ocr_pages') else ''} "
              f"{doc['stats']} in {time.monotonic() - started:.1f}s")
        if adapter == "docx":
            docx_id = doc["id"]
    return docx_id


def smoke_publish(base: str, token: str, pid: str, doc_id: str) -> None:
    """Generate + render the DOCX video, approve it and publish to a mock YouTube."""
    status, job = request(base, "POST", f"/projects/{pid}/storyboards", token, body={
        "document_id": doc_id,
        "settings": {"style": "explainer_16x9", "target_duration_s": 10, "max_scenes": 3}})
    assert status == 202, status
    job = wait_job(base, token, job, TIMEOUT_S)
    assert job["status"] == "succeeded", job
    vid = job["result"]["storyboard_id"]
    status, res = request(base, "POST", f"/storyboards/{vid}/render", token, body={})
    assert status == 202, status
    job = wait_job(base, token, res["job"], RENDER_TIMEOUT_S)
    assert job["status"] == "succeeded", job.get("error") or job
    status, video = request(base, "POST", f"/videos/{vid}/actions", token,
                            body={"action": "approve", "note": "smoke"})
    assert status == 200 and video["status"] == "approved", (status, video)
    status, acc = request(base, "POST", "/social/accounts/mock", token,
                          body={"platform": "youtube"})
    assert status == 201, status
    status, pubs = request(base, "POST", f"/videos/{vid}/publications", token, body={
        "items": [{"account_id": acc["id"], "schedule": {"mode": "now"}}]})
    assert status == 201, (status, pubs)
    pub = pubs[0]
    deadline = time.monotonic() + TIMEOUT_S
    while pub["status"] not in ("published", "failed"):
        assert time.monotonic() < deadline, f"publication stuck in {pub['status']}"
        time.sleep(0.5)
        pub = request(base, "GET", f"/publications/{pub['id']}", token)[1]
    assert pub["status"] == "published", pub
    status, page = request(base, "GET", pub["remote_url"], None)
    assert status == 200, status
    assert "Hướng dẫn trồng cà phê" in page.decode("utf-8"), page[:300]
    status, video = request(base, "GET", f"/videos/{vid}", token)
    assert video["status"] == "published", video["status"]
    print(f"publish ok: mock YouTube {pub['remote_url'].split('?')[0]} "
          f"(thumbnail {'yes' if pub['meta'].get('thumbnail') else 'no'})")


def main() -> int:
    argv = sys.argv[1:]
    fixtures = None
    if "--documents" in argv:
        i = argv.index("--documents")
        fixtures = Path(argv[i + 1]).resolve()
        del argv[i:i + 2]
    args = [a for a in argv if not a.startswith("--")]
    render = "--render" in argv
    publish = "--publish" in argv
    exe = Path(args[0]).resolve()
    if not exe.is_file():
        print(f"Sidecar not found: {exe}")
        return 2
    token = secrets.token_urlsafe(32)
    data_dir = Path(tempfile.mkdtemp(prefix="vs-smoke-")) / "Dữ liệu thử nghiệm"
    env = {
        **os.environ,
        "VIDEOSTUDIO_TOKEN": token,
        "VIDEOSTUDIO_DATA_DIR": str(data_dir),
        "VIDEOSTUDIO_PARENT_PID": str(os.getpid()),
        "PYTHONUTF8": "1",
    }
    env.pop("VIDEOSTUDIO_DEV", None)
    creationflags = 0x08000000 if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [str(exe), "--port", "0"],
        cwd=exe.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    port: list[int] = []
    output: list[str] = []

    def reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            output.append(line)
            if line.startswith("VIDEOSTUDIO_PORT=") and not port:
                port.append(int(line.split("=", 1)[1]))

    threading.Thread(target=reader, daemon=True).start()
    started = time.monotonic()
    try:
        while not port:
            if proc.poll() is not None:
                raise RuntimeError(f"sidecar exited with {proc.returncode}")
            if time.monotonic() - started > TIMEOUT_S:
                raise RuntimeError("timed out waiting for port")
            time.sleep(0.1)
        base = f"http://127.0.0.1:{port[0]}"
        status = None
        while time.monotonic() - started < TIMEOUT_S:
            try:
                status, _ = request(base, "GET", "/health", token)
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.2)
        assert status == 200, f"/health with token -> {status}"
        print(f"ready on port {port[0]} after {time.monotonic() - started:.1f}s")

        assert request(base, "GET", "/health", None)[0] == 401, "expected 401 without token"
        assert request(base, "GET", "/health", "wrong")[0] == 401, "expected 401 with bad token"

        status, info = request(base, "GET", "/system/info", token)
        assert status == 200 and info["frozen"] is True, info
        assert info["utf8_mode"] is True, "UTF-8 mode not enabled in frozen app"
        print(f"system: {info['platform']} python={info['python']} "
              f"secret_store={info['secret_store']} ffmpeg={info['ffmpeg']['found']}")

        status, project = request(base, "POST", "/projects", token, body={"name": "Dự án thử"})
        assert status == 201, status
        pid = project["id"]
        script = "Cảnh 1: Mở đầu\nXin chào các bạn.\n\nCảnh 2: Kết thúc\nHẹn gặp lại!"
        status, _ = request(base, "POST", f"/projects/{pid}/sources", token,
                            form={"text": script, "adapter_id": "script"})
        assert status == 201, status
        status, job = request(base, "POST", f"/projects/{pid}/ingest", token, body={})
        assert status == 202, status
        while job["status"] not in ("succeeded", "failed", "cancelled"):
            time.sleep(0.2)
            job = request(base, "GET", f"/jobs/{job['id']}", token)[1]
        assert job["status"] == "succeeded", job
        doc = job["result"]["documents"][0]
        assert doc["title"] == "Cảnh 1: Mở đầu", doc
        print(f"ingest ok: {doc['stats']}")

        if render:
            assert info["ffmpeg"]["found"], "FFmpeg not found by the sidecar"
            smoke_render(base, token, pid, doc["id"])

        if fixtures is not None:
            docx_id = smoke_documents(base, token, pid, fixtures)
            if publish:
                smoke_publish(base, token, pid, docx_id)

        status, res = request(base, "POST", "/secrets/selftest", token,
                              body={"value": "bí mật 123"})
        assert status == 200 and res["ok"], res
        print(f"secret store ok ({res['backend']})")
        print("SMOKE TEST PASSED")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE TEST FAILED: {exc}")
        print("---- sidecar output ----")
        print("\n".join(output[-80:]))
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
