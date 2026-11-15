from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from videostudio.api import create_app
from videostudio.config import Settings
from videostudio.db import make_engine, make_session_factory, run_migrations
from videostudio.plugins import JobContext

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "test-token-123"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    # Spaces and Vietnamese diacritics on purpose: paths like this are common on Windows.
    d = tmp_path / "Dữ liệu Video Studio"
    d.mkdir()
    return d


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    s = Settings(data_dir=data_dir, token=TOKEN, dev=False, workers=1)
    s.ensure_dirs()
    return s


@pytest.fixture
def session_factory(settings: Settings):
    engine = make_engine(settings.db_path)
    run_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def ctx(tmp_path: Path) -> JobContext:
    progress: list[int] = []
    c = JobContext(job_id="test", project_dir=tmp_path, progress_cb=lambda p, m: progress.append(p))
    c.progress_values = progress  # type: ignore[attr-defined]
    return c
