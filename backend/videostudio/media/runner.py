"""Run ffmpeg with progress reporting and cancellation (thread + Popen, so it works on
any asyncio event loop on Windows)."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from .ffmpeg import CREATE_NO_WINDOW


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr_tail: str = ""):
        super().__init__(message)
        self.stderr_tail = stderr_tail


class FFmpegCancelled(Exception):
    pass


def _run_blocking(args: list[str], cwd: Path | None, total_s: float | None,
                  on_progress: Callable[[float], None] | None,
                  is_cancelled: Callable[[], bool] | None) -> None:
    with tempfile.TemporaryFile(mode="w+b") as err:
        proc = subprocess.Popen(
            [args[0], "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats",
             *args[1:]],
            cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=err,
            stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        cancelled = threading.Event()

        def watchdog() -> None:
            while proc.poll() is None:
                if is_cancelled and is_cancelled():
                    cancelled.set()
                    proc.kill()
                    return
                threading.Event().wait(0.2)

        threading.Thread(target=watchdog, daemon=True).start()
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if on_progress and total_s and line.startswith("out_time_us="):
                try:
                    seconds = int(line.split("=", 1)[1]) / 1e6
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, seconds / total_s)))
        code = proc.wait()
        if cancelled.is_set():
            raise FFmpegCancelled()
        if code != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace")[-3000:]
            last = [ln for ln in tail.strip().splitlines() if ln.strip()][-1:] or ["?"]
            raise FFmpegError(f"ffmpeg lỗi (mã {code}): {last[0][:300]}", tail)


async def run_ffmpeg(args: list[str], *, cwd: Path | None = None, total_s: float | None = None,
                     on_progress: Callable[[float], None] | None = None,
                     is_cancelled: Callable[[], bool] | None = None) -> None:
    """``args[0]`` is the ffmpeg executable; common flags are added automatically."""
    await asyncio.to_thread(_run_blocking, args, cwd, total_s, on_progress, is_cancelled)
