"""Provider instances: secrets only in the SecretStore, masking, defaults, resolution."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from videostudio.providers.service import (
    MASK,
    ProviderNotFound,
    ProviderService,
    ProviderValidationError,
    mask_secret,
    secret_key,
)

SECRET = "sk-live-SUPERSECRET-9876"


def _db_dump(settings) -> str:
    con = sqlite3.connect(settings.db_path)
    try:
        return "\n".join(str(row) for line in con.iterdump() for row in [line])
    finally:
        con.close()


def _all_bytes_under(path) -> bytes:
    return b"".join(p.read_bytes() for p in path.rglob("*") if p.is_file())


def test_mask_secret():
    assert mask_secret("abcd1234") == MASK + "1234"
    assert mask_secret("short") == MASK
    assert mask_secret("") is None and mask_secret(None) is None


def test_seeded_defaults(client: TestClient, auth):
    r = client.get("/providers", headers=auth).json()
    types = {i["type"]: i for i in r["instances"]}
    assert {"mock_llm", "edge_tts", "mock_tts", "proportional", "mock_media"} <= set(types)
    assert r["defaults"]["llm"] == types["mock_llm"]["id"]
    assert r["defaults"]["tts"] == types["edge_tts"]["id"]
    assert types["edge_tts"]["unofficial"] is True
    assert types["mock_llm"]["is_mock"] is True


def test_types_endpoint(client: TestClient, auth):
    r = client.get("/providers/types", headers=auth).json()
    ids = {t["id"]: t for t in r["types"]}
    assert {"openai", "anthropic", "gemini", "ollama", "openai_compatible", "azure_tts",
            "google_tts", "elevenlabs", "fpt_tts", "openai_tts", "edge_tts", "piper",
            "faster_whisper", "proportional", "pexels"} <= set(ids)
    assert ids["openai"]["secret_fields"] == ["api_key"]
    assert ids["openai"]["config_schema"]["properties"]["api_key"]["x-secret"] is True
    assert [c["id"] for c in r["capabilities"]] == ["llm", "tts", "alignment", "media", "ocr"]
    assert {"tesseract", "google_vision", "azure_vision", "mock_ocr"} <= set(ids)
    only_tts = client.get("/providers/types?capability=tts", headers=auth).json()["types"]
    assert {t["capability"] for t in only_tts} == {"tts"}


def test_secret_never_in_db_api_or_logs(client: TestClient, auth, settings, caplog):
    caplog.set_level("DEBUG")
    r = client.post("/providers", headers=auth, json={
        "type": "openai", "name": "OpenAI của tôi",
        "config": {"api_key": SECRET, "model": "gpt-4o-mini", "bogus": "dropped"}})
    assert r.status_code == 201, r.text
    inst = r.json()
    assert SECRET not in r.text
    assert inst["secrets"]["api_key"] == MASK + "9876"
    assert "api_key" not in inst["config"] and "bogus" not in inst["config"]

    for url in ("/providers", f"/providers/{inst['id']}", "/providers?capability=llm"):
        assert SECRET not in client.get(url, headers=auth).text

    # DB contains no plaintext, the SecretStore files contain no plaintext either
    assert SECRET not in _db_dump(settings)
    assert SECRET.encode() not in _all_bytes_under(settings.data_dir)
    assert SECRET not in caplog.text

    svc: ProviderService = client.app.state.services.providers
    assert client.app.state.services.secrets.get(secret_key(inst["id"], "api_key")) == SECRET
    _, ctx, _ = svc.context(inst["id"])
    assert ctx.config["api_key"] == SECRET


def test_update_keeps_masked_secret_and_can_replace_or_clear(client: TestClient, auth):
    inst = client.post("/providers", headers=auth, json={
        "type": "pexels", "config": {"api_key": SECRET}}).json()
    store = client.app.state.services.secrets
    key = secret_key(inst["id"], "api_key")

    # UI sends back the masked value → keep
    r = client.patch(f"/providers/{inst['id']}", headers=auth, json={
        "name": "Pexels 2", "config": {"api_key": inst["secrets"]["api_key"], "locale": "en-US"}})
    assert r.status_code == 200, r.text
    assert store.get(key) == SECRET and r.json()["config"]["locale"] == "en-US"
    # omitted → keep
    client.patch(f"/providers/{inst['id']}", headers=auth, json={"config": {"locale": "vi-VN"}})
    assert store.get(key) == SECRET
    # replaced
    client.patch(f"/providers/{inst['id']}", headers=auth,
                 json={"config": {"api_key": "new-key-0000"}})
    assert store.get(key) == "new-key-0000"
    # explicit empty string on a required secret → validation error, secret unchanged
    r = client.patch(f"/providers/{inst['id']}", headers=auth, json={"config": {"api_key": ""}})
    assert r.status_code == 422 and store.get(key) == "new-key-0000"


def test_required_fields_and_validation(client: TestClient, auth):
    r = client.post("/providers", headers=auth, json={"type": "openai", "config": {}})
    assert r.status_code == 422 and "API key" in r.text
    r = client.post("/providers", headers=auth, json={
        "type": "fpt_tts", "config": {"api_key": "k" * 10, "default_voice": "nope"}})
    assert r.status_code == 422
    r = client.post("/providers", headers=auth, json={
        "type": "fpt_tts", "config": {"api_key": "k" * 10, "speed": 9}})
    assert r.status_code == 422
    r = client.post("/providers", headers=auth, json={"type": "nope", "config": {}})
    assert r.status_code == 404


def test_delete_removes_secret_and_reassigns_default(client: TestClient, auth):
    svc = client.app.state.services
    a = client.post("/providers", headers=auth, json={
        "type": "azure_tts", "config": {"api_key": SECRET, "region": "eastasia"},
        "make_default": True}).json()
    assert client.get("/providers", headers=auth).json()["defaults"]["tts"] == a["id"]
    assert client.delete(f"/providers/{a['id']}", headers=auth).status_code == 204
    assert svc.secrets.get(secret_key(a["id"], "api_key")) is None
    defaults = client.get("/providers", headers=auth).json()["defaults"]
    assert defaults["tts"] is not None and defaults["tts"] != a["id"]
    assert client.get(f"/providers/{a['id']}", headers=auth).status_code == 404


def test_switch_default_without_restart(client: TestClient, auth):
    insts = client.get("/providers?capability=tts", headers=auth).json()["instances"]
    mock = next(i for i in insts if i["type"] == "mock_tts")
    r = client.post(f"/providers/{mock['id']}/default", headers=auth)
    assert r.status_code == 200 and r.json()["is_default"]
    defaults = client.get("/providers", headers=auth).json()["defaults"]
    assert defaults["tts"] == mock["id"]
    resolved = client.app.state.services.providers.resolve("tts")
    assert resolved.provider.id == "mock_tts"
    assert sum(i["is_default"] for i in client.get("/providers?capability=tts",
                                                   headers=auth).json()["instances"]) == 1


def test_resolution_order(client: TestClient, auth):
    svc: ProviderService = client.app.state.services.providers
    ollama = svc.create("ollama", "Ollama", {"model": "qwen2.5:7b"})
    default_llm = svc.defaults()["llm"]
    assert ollama["is_default"] is False  # mock stays default

    r = svc.resolve("llm")
    assert r.instance_id == default_llm
    # project override wins over default
    r = svc.resolve("llm", None, {"instance_id": ollama["id"]})
    assert r.instance_id == ollama["id"] and r.model == "qwen2.5:7b"
    # dialog choice wins over project; model override applies
    r = svc.resolve("llm", {"instance_id": ollama["id"], "model": "llama3.2"},
                    {"instance_id": default_llm})
    assert r.instance_id == ollama["id"] and r.model == "llama3.2"
    # model-only choice applies to the default instance
    r = svc.resolve("llm", {"model": "mock-2"})
    assert r.instance_id == default_llm and r.model == "mock-2"
    # wrong capability
    tts_id = svc.defaults()["tts"]
    with pytest.raises(ProviderValidationError):
        svc.resolve("llm", {"instance_id": tts_id})
    with pytest.raises(ProviderNotFound):
        svc.resolve("llm", {"instance_id": "missing"})


def test_test_connection_endpoints(client: TestClient, auth):
    insts = client.get("/providers", headers=auth).json()["instances"]
    mock_llm = next(i for i in insts if i["type"] == "mock_llm")
    r = client.post(f"/providers/{mock_llm['id']}/test", headers=auth).json()
    assert r["ok"] is True
    # unsaved form, provider error is reported as ok=false (not a 500)
    r = client.post("/providers/test", headers=auth, json={
        "type": "openai_compatible", "config": {"base_url": "http://127.0.0.1:9/v1",
                                                "model": "x", "timeout_s": 5}}).json()
    assert r["ok"] is False and r["message"]
    models = client.get(f"/providers/{mock_llm['id']}/models", headers=auth).json()
    assert models["models"][0]["id"] == "mock-1"


def test_voices_and_sample(client: TestClient, auth):
    insts = client.get("/providers?capability=tts", headers=auth).json()["instances"]
    mock = next(i for i in insts if i["type"] == "mock_tts")
    voices = client.get(f"/providers/{mock['id']}/voices", headers=auth).json()
    assert {v["id"] for v in voices["voices"]} >= {"beep-low", "beep-high"}
    r = client.post(f"/providers/{mock['id']}/sample", headers=auth,
                    json={"text": "Xin chào Việt Nam", "voice": "beep-high"})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert "test-token" not in url
    audio = client.get(url)
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    # media key required
    assert client.get(url.split("?")[0]).status_code == 401
    # second call hits the cache
    assert client.post(f"/providers/{mock['id']}/sample", headers=auth,
                       json={"text": "Xin chào Việt Nam", "voice": "beep-high"}).json()["cached"]


def test_media_route_cannot_escape_data_dir(client: TestClient, auth):
    from videostudio.api.media import media_key

    k = media_key(client.app.state.services.settings.token)
    for bad in ("../../etc/passwd", "secrets/x.secret", "app.db", "logs/backend.log"):
        assert client.get(f"/media/{bad}?k={k}").status_code == 404
