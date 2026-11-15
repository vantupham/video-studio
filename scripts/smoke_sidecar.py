"""Smoke-test the frozen backend sidecar exactly like the Tauri shell launches it.

    python scripts/smoke_sidecar.py <path to videostudio-backend[.exe]>

Starts the executable with a random token and ``--port 0``, reads the port from
stdout, then checks auth, migrations (project CRUD), an ingest job with Vietnamese
text and the secret store. Uses only the standard library.
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


def request(base: str, method: str, path: str, token: str | None, body=None, form=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if form is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for key, value in form.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            )
        parts.append(f"--{boundary}--\r\n")
        data = "".join(parts).encode("utf-8")
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            return res.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        return err.code, None


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
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
