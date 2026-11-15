from __future__ import annotations

from pathlib import Path

from videostudio.plugins import (
    InputAdapter,
    LLMProvider,
    Publisher,
    Renderer,
    SourceRef,
    TTSProvider,
    build_registry,
)

USER_PLUGIN = '''
from videostudio.ir import ContentIR, IRSource, ParagraphBlock, Section


class CsvAdapter:
    id = "csv"
    name = "CSV"
    description = "test"
    file_extensions = (".csv",)

    def can_handle(self, source):
        return source.suffix == ".csv"

    def config_schema(self):
        return {"type": "object", "properties": {}}

    async def ingest(self, source, ctx):
        return ContentIR(title="csv", source=IRSource(type="other", uri=source.uri),
                         sections=[Section(id="s1", blocks=[ParagraphBlock(text="ok")])])


def register(registry):
    registry.add_input_adapter(CsvAdapter())
'''


def test_builtin_adapters_implement_protocol() -> None:
    reg = build_registry()
    assert {"script", "markdown"} <= set(reg.input_adapters)
    for adapter in reg.input_adapters.values():
        assert isinstance(adapter, InputAdapter)
        schema = adapter.config_schema()
        assert schema["type"] == "object" and "properties" in schema


def test_directory_plugins_loaded_and_errors_isolated(tmp_path: Path) -> None:
    (tmp_path / "csv_plugin.py").write_text(USER_PLUGIN, encoding="utf-8")
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    pkg = tmp_path / "bad_type"
    pkg.mkdir()
    (pkg / "plugin.py").write_text(
        "def register(r):\n    r.add_input_adapter(object())\n", encoding="utf-8"
    )
    reg = build_registry(tmp_path)
    assert "csv" in reg.input_adapters
    assert reg.origins["input_adapter:csv"] == "dir:csv_plugin.py"
    origins = sorted(e.origin for e in reg.errors)
    assert origins == ["dir:bad_type", "dir:broken.py"]
    found = reg.find_input_adapter(SourceRef(type="file", uri="x.csv", filename="data.csv"))
    assert found is not None and found.id == "csv"


def test_stub_protocols_are_runtime_checkable() -> None:
    class FakePublisher:
        id = "fake"
        name = "Fake"
        platform = "youtube"

        def metadata_schema(self):
            return {}

        def constraints(self, options=None):
            ...

        async def publish(self, req, pctx):
            ...

        async def status(self, remote_id, pctx):
            ...

    class Incomplete:
        id = "x"
        name = "X"
        platform = "youtube"

        async def publish(self, req, pctx):
            ...

    assert isinstance(FakePublisher(), Publisher)
    assert not isinstance(Incomplete(), Publisher)
    assert not isinstance(FakePublisher(), Renderer)
    assert not isinstance(object(), LLMProvider)
    assert not isinstance(object(), TTSProvider)
