from __future__ import annotations

import json
import re

import jsonschema
import pytest
from pydantic import ValidationError

from videostudio.ir import BLOCK_KINDS, ContentIR, load_json_schema
from videostudio.plugins import JobContext, SourceRef
from videostudio.plugins.adapters.markdown import MarkdownAdapter
from videostudio.plugins.adapters.script import ScriptAdapter

from .conftest import FIXTURES, REPO_ROOT

SCHEMA_DIR = REPO_ROOT / "packages" / "ir-schema"


@pytest.fixture(scope="module")
def schema() -> dict:
    s = load_json_schema()
    assert s is not None, "content-ir.schema.json not found"
    jsonschema.Draft202012Validator.check_schema(s)
    return s


def validate(schema: dict, data: dict) -> None:
    jsonschema.Draft202012Validator(schema).validate(data)


def test_examples_validate(schema: dict) -> None:
    examples = sorted((SCHEMA_DIR / "examples").glob("*.json"))
    assert examples
    for path in examples:
        data = json.loads(path.read_text(encoding="utf-8"))
        validate(schema, data)
        ContentIR.model_validate(data)


@pytest.mark.parametrize(
    ("adapter", "fixture"),
    [(MarkdownAdapter(), "bài học tiếng việt.md"), (ScriptAdapter(), "kịch bản mẫu.txt")],
)
async def test_adapter_output_matches_json_schema(schema: dict, adapter, fixture, tmp_path):
    path = FIXTURES / fixture
    ir = await adapter.ingest(
        SourceRef(type="file", uri=path.name, path=path, filename=path.name),
        JobContext(job_id="t", project_dir=tmp_path),
    )
    data = ir.model_dump(mode="json")
    validate(schema, data)
    assert ContentIR.model_validate(data) == ir


def test_block_kinds_in_sync(schema: dict) -> None:
    refs = [r["$ref"].split("/")[-1] for r in schema["$defs"]["Block"]["oneOf"]]
    schema_kinds = [schema["$defs"][r]["properties"]["kind"]["const"] for r in refs]
    assert schema_kinds == list(BLOCK_KINDS)

    ts = (SCHEMA_DIR / "ts" / "content-ir.ts").read_text(encoding="utf-8")
    ts_kinds = re.findall(r'kind: "([a-z_]+)"', ts)
    assert ts_kinds == list(BLOCK_KINDS)


def test_pydantic_fields_match_schema(schema: dict) -> None:
    top = set(schema["properties"])
    assert top == set(ContentIR.model_fields)


@pytest.mark.parametrize(
    "bad",
    [
        {"schema_version": "1.0", "title": "x", "sections": []},  # missing source
        {"schema_version": "1.0", "title": "x", "source": {"type": "nope", "uri": ""},
         "sections": []},
        {"schema_version": "1.0", "title": "x", "source": {"type": "script", "uri": ""},
         "sections": [{"id": "s1", "blocks": [{"kind": "video", "text": "?"}]}]},
        {"schema_version": "1.0", "title": "x", "source": {"type": "script", "uri": ""},
         "sections": [{"id": "s1", "blocks": [{"kind": "heading", "text": "h", "level": 9}]}]},
    ],
)
def test_invalid_documents_rejected(schema: dict, bad: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate(schema, bad)
    with pytest.raises(ValidationError):
        ContentIR.model_validate(bad)
