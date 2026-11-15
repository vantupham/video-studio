"""Locate ffmpeg/ffprobe, probe media files and detect hardware H.264 encoders.

Lookup order for the binaries:
1. ``VIDEOSTUDIO_FFMPEG_DIR`` (set by the Tauri shell to ``<resources>/ffmpeg``).
2. ``ffmpeg/`` next to the frozen backend folder (``<install>/backend/../ffmpeg``).
3. ``<data_dir>/bin`` (user-provided).
4. ``PATH``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

EXE = ".exe" if sys.platform == "win32" else ""
# Hide console windows for child processes on Windows.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class FFmpegNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class FFmpegInfo:
    found: bool
    ffmpeg: str | None
    ffprobe: str | None
    version: str | None
    source: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _candidate_dirs(extra_dirs: list[Path]) -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = []
    env_dir = os.environ.get("VIDEOSTUDIO_FFMPEG_DIR")
    if env_dir:
        dirs.append(("env", Path(env_dir)))
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        dirs.append(("bundled", exe_dir.parent / "ffmpeg"))
        dirs.append(("bundled", exe_dir / "ffmpeg"))
    dirs.extend(("data_dir", d) for d in extra_dirs)
    return dirs


def find_binary(name: str, extra_dirs: list[Path] | None = None) -> tuple[str, str] | None:
    """Return ``(path, source)`` for ``ffmpeg`` / ``ffprobe`` or ``None``."""
    for source, directory in _candidate_dirs(extra_dirs or []):
        candidate = directory / f"{name}{EXE}"
        if candidate.is_file():
            return str(candidate), source
    found = shutil.which(name)
    return (found, "path") if found else None


def _run(args: list[str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )


def detect_ffmpeg(extra_dirs: list[Path] | None = None) -> FFmpegInfo:
    ffmpeg = find_binary("ffmpeg", extra_dirs)
    ffprobe = find_binary("ffprobe", extra_dirs)
    if not ffmpeg:
        return FFmpegInfo(False, None, ffprobe[0] if ffprobe else None, None, None)
    version = None
    try:
        out = _run([ffmpeg[0], "-hide_banner", "-version"], timeout=10).stdout
        version = out.splitlines()[0] if out else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return FFmpegInfo(True, ffmpeg[0], ffprobe[0] if ffprobe else None, version, ffmpeg[1])


def require_ffmpeg(extra_dirs: list[Path] | None = None) -> FFmpegInfo:
    info = detect_ffmpeg(extra_dirs)
    if not info.found:
        raise FFmpegNotFound("ffmpeg was not found (bundled, data dir or PATH)")
    return info


def probe(path: Path, ffprobe: str | None = None) -> dict[str, Any]:
    """Return ffprobe's JSON (format + streams) for a media file."""
    exe = ffprobe or (find_binary("ffprobe") or (None, None))[0]
    if not exe:
        raise FFmpegNotFound("ffprobe was not found")
    proc = _run(
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


@dataclass(frozen=True)
class VideoEncoder:
    name: str  # ffmpeg codec name, e.g. "h264_nvenc"
    vendor: str  # "nvidia" | "intel" | "amd" | "software"
    hardware: bool


HW_H264_ENCODERS = (
    VideoEncoder("h264_nvenc", "nvidia", True),
    VideoEncoder("h264_qsv", "intel", True),
    VideoEncoder("h264_amf", "amd", True),
)
SOFTWARE_H264 = VideoEncoder("libx264", "software", False)


def _encoder_works(ffmpeg: str, codec: str) -> bool:
    # Being listed in `-encoders` is not enough (driver/GPU may be missing): encode one frame.
    proc = _run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=256x256:d=0.1", "-frames:v", "1", "-c:v", codec, "-f", "null", "-"],
        timeout=30,
    )
    return proc.returncode == 0


@lru_cache(maxsize=4)
def detect_h264_encoders(ffmpeg: str, verify: bool = True) -> tuple[VideoEncoder, ...]:
    """Usable H.264 encoders, hardware first, always ending with libx264 if available."""
    try:
        listing = _run([ffmpeg, "-hide_banner", "-encoders"], timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ()
    usable = [
        enc for enc in HW_H264_ENCODERS
        if f" {enc.name} " in listing and (not verify or _encoder_works(ffmpeg, enc.name))
    ]
    if f" {SOFTWARE_H264.name} " in listing:
        usable.append(SOFTWARE_H264)
    return tuple(usable)


def pick_h264_encoder(ffmpeg: str, prefer_hardware: bool = True) -> VideoEncoder:
    encoders = detect_h264_encoders(ffmpeg)
    if prefer_hardware:
        for enc in encoders:
            if enc.hardware:
                return enc
    return SOFTWARE_H264
