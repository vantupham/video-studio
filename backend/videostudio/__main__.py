"""Entry point: ``python -m videostudio`` and the PyInstaller sidecar executable.

The Tauri shell starts this process with:
  VIDEOSTUDIO_TOKEN=<random per launch>  VIDEOSTUDIO_PARENT_PID=<tauri pid>
  PYTHONUTF8=1  videostudio-backend --port <free port>
and prints ``VIDEOSTUDIO_PORT=<port>`` on stdout once the port is bound.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import socket
import sys
import threading
from pathlib import Path

from videostudio import __version__
from videostudio.config import (
    DEV_TOKEN,
    ENV_DATA_DIR,
    ENV_DEV,
    ENV_PARENT_PID,
    ENV_TOKEN,
    Settings,
)

HOST = "127.0.0.1"


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def setup_logging(settings: Settings, level: str) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        settings.logs_dir / "backend.log", maxBytes=5 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [file_handler]
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        handlers.append(stream)
    logging.basicConfig(level=level.upper(), handlers=handlers, force=True)
    logging.getLogger("alembic").setLevel(logging.WARNING)


def _parent_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x102
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start_parent_watchdog(pid: int, interval: float = 2.0) -> None:
    """Exit if the Tauri shell disappears (crash / task-kill) so no orphan stays alive."""

    def watch() -> None:
        stop = threading.Event()
        while not stop.wait(interval):
            if not _parent_alive(pid):
                logging.getLogger(__name__).warning("Parent %s is gone, exiting", pid)
                os._exit(0)

    threading.Thread(target=watch, name="parent-watchdog", daemon=True).start()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="videostudio-backend")
    parser.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    parser.add_argument("--dev", action="store_true", help=f"allow the '{DEV_TOKEN}' token")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    _force_utf8_stdio()
    args = parse_args(argv)
    if args.dev:
        os.environ[ENV_DEV] = "1"
    if args.data_dir:
        os.environ[ENV_DATA_DIR] = str(args.data_dir.expanduser().resolve())
    if args.reload and not os.environ.get(ENV_TOKEN):
        os.environ[ENV_DEV] = "1"

    settings = Settings.from_env()
    settings.ensure_dirs()
    setup_logging(settings, args.log_level)
    if settings.parent_pid:
        start_parent_watchdog(settings.parent_pid)

    import uvicorn

    if args.reload:
        port = args.port or 8765
        print(f"VIDEOSTUDIO_PORT={port}", flush=True)
        uvicorn.run(
            "videostudio.api.app:create_app",
            factory=True,
            host=HOST,
            port=port,
            reload=True,
            reload_dirs=[str(Path(__file__).resolve().parent)],
            log_level=args.log_level,
        )
        return

    from videostudio.api import create_app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, args.port))
    port = sock.getsockname()[1]
    print(f"VIDEOSTUDIO_PORT={port}", flush=True)
    if os.environ.get(ENV_PARENT_PID) is None:
        logging.getLogger(__name__).info("Listening on http://%s:%s", HOST, port)

    config = uvicorn.Config(
        create_app(settings),
        log_config=None,
        log_level=args.log_level,
        loop="asyncio",
        http="h11",
        ws="auto",
        lifespan="on",
    )
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
