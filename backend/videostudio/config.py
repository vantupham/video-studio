"""Runtime configuration, read from environment variables.

The Tauri shell passes configuration to the sidecar through env vars so that the
per-launch token never appears on the command line (visible in process lists).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "VideoStudio"
DEV_TOKEN = "dev-token"

ENV_DATA_DIR = "VIDEOSTUDIO_DATA_DIR"
ENV_TOKEN = "VIDEOSTUDIO_TOKEN"
ENV_DEV = "VIDEOSTUDIO_DEV"
ENV_WORKERS = "VIDEOSTUDIO_WORKERS"
ENV_FFMPEG_DIR = "VIDEOSTUDIO_FFMPEG_DIR"
ENV_PARENT_PID = "VIDEOSTUDIO_PARENT_PID"
ENV_ALLOWED_ORIGINS = "VIDEOSTUDIO_ALLOWED_ORIGINS"

# Origins used by the Tauri webview (Windows / macOS+Linux) and the Vite dev server.
DEFAULT_ALLOWED_ORIGINS = (
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
)

SUBDIRS = ("projects", "styles", "recipes", "plugins", "cache", "models", "logs", "secrets")


def default_data_dir() -> Path:
    # roaming=False -> %LOCALAPPDATA%\VideoStudio on Windows, no "author" level.
    return Path(user_data_dir(APP_NAME, appauthor=False, roaming=False))


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token: str
    dev: bool = False
    workers: int = 2
    ffmpeg_dir: Path | None = None
    parent_pid: int | None = None
    allowed_origins: tuple[str, ...] = field(default=DEFAULT_ALLOWED_ORIGINS)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def plugins_dir(self) -> Path:
        return self.data_dir / "plugins"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    def project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def ensure_dirs(self) -> None:
        for sub in SUBDIRS:
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        dev = _truthy(env.get(ENV_DEV))
        token = env.get(ENV_TOKEN) or ""
        if not token:
            if not dev:
                raise RuntimeError(
                    f"{ENV_TOKEN} is not set. Start with --dev to use the development token."
                )
            token = DEV_TOKEN
        data_dir = Path(env[ENV_DATA_DIR]) if env.get(ENV_DATA_DIR) else default_data_dir()
        ffmpeg_dir = Path(env[ENV_FFMPEG_DIR]) if env.get(ENV_FFMPEG_DIR) else None
        parent_pid = int(env[ENV_PARENT_PID]) if env.get(ENV_PARENT_PID, "").isdigit() else None
        origins = DEFAULT_ALLOWED_ORIGINS
        if env.get(ENV_ALLOWED_ORIGINS):
            extra = tuple(o.strip() for o in env[ENV_ALLOWED_ORIGINS].split(",") if o.strip())
            origins = origins + extra
        return cls(
            data_dir=data_dir.expanduser().resolve(),
            token=token,
            dev=dev,
            workers=max(1, int(env.get(ENV_WORKERS, "2") or 2)),
            ffmpeg_dir=ffmpeg_dir,
            parent_pid=parent_pid,
            allowed_origins=origins,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
