"""The example provider plugin in docs/examples loads through the plugin directory and
works end to end (so the documentation cannot silently rot)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import httpx
import respx

from videostudio.plugins.registry import build_registry
from videostudio.providers.base import ProviderContext
from videostudio.providers.service import ProviderService
from videostudio.secretstore import create_secret_store

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "examples" / "http_tts_plugin.py"


async def test_example_tts_plugin(tmp_path, monkeypatch, session_factory, settings):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    shutil.copy(EXAMPLE, plugins / EXAMPLE.name)
    registry = build_registry(plugins)
    assert "example_http_tts" in registry.providers
    assert not [e for e in registry.errors if "http_tts" in e.origin], registry.errors

    svc = ProviderService(session_factory, registry.providers,
                          create_secret_store(settings.secrets_dir))
    inst = svc.create("example_http_tts", None,
                      {"base_url": "http://tts.local", "api_key": "tok-plugin-secret-9876"},
                      False)
    assert inst["secrets"]["api_key"] == "••••9876"
    assert "tok-plugin" not in str(inst["config"])

    provider, ctx, _ = svc.context(inst["id"])
    monkeypatch.setattr(sys.modules[type(provider).__module__], "media_duration",
                        lambda path: 2.0)
    with respx.mock() as mock:
        voices = mock.get("http://tts.local/voices").mock(return_value=httpx.Response(
            200, json=[{"id": "v1", "name": "Giọng 1", "lang": "vi"}]))
        tts = mock.post("http://tts.local/tts").mock(
            return_value=httpx.Response(200, content=b"ID3mp3"))
        result = await provider.test_connection(ctx)
        audio = await provider.synthesize(ctx, "Xin chào", voice="v1", lang="vi",
                                          out_path=tmp_path / "a.mp3")
    assert result.ok and "1 giọng" in result.message
    assert voices.calls[0].request.headers["Authorization"] == "Bearer tok-plugin-secret-9876"
    assert tts.called and audio.duration_s == 2.0
    assert isinstance(ctx, ProviderContext)
