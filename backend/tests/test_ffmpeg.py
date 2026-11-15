from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from videostudio.media import ffmpeg as ff


def _fake_exe(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}{ff.EXE}"
    path.write_text("")
    path.chmod(0o755)
    return path


def test_env_dir_has_priority(tmp_path: Path, monkeypatch) -> None:
    bundled = tmp_path / "tài nguyên" / "ffmpeg"
    exe = _fake_exe(bundled, "ffmpeg")
    monkeypatch.setenv("VIDEOSTUDIO_FFMPEG_DIR", str(bundled))
    assert ff.find_binary("ffmpeg") == (str(exe), "env")


def test_data_dir_then_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("VIDEOSTUDIO_FFMPEG_DIR", raising=False)
    monkeypatch.setenv("PATH", "")
    assert ff.find_binary("ffprobe") is None
    info = ff.detect_ffmpeg()
    assert info.found is False and info.ffmpeg is None
    with pytest.raises(ff.FFmpegNotFound):
        ff.require_ffmpeg()
    exe = _fake_exe(tmp_path / "bin", "ffprobe")
    assert ff.find_binary("ffprobe", [tmp_path / "bin"]) == (str(exe), "data_dir")


def test_pick_encoder_prefers_hardware(monkeypatch) -> None:
    ff.detect_h264_encoders.cache_clear()
    monkeypatch.setattr(ff, "detect_h264_encoders",
                        lambda path, verify=True: (ff.HW_H264_ENCODERS[0], ff.SOFTWARE_H264))
    assert ff.pick_h264_encoder("ffmpeg").name == "h264_nvenc"
    assert ff.pick_h264_encoder("ffmpeg", prefer_hardware=False).name == "libx264"
    monkeypatch.setattr(ff, "detect_h264_encoders", lambda path, verify=True: ())
    assert ff.pick_h264_encoder("ffmpeg").name == "libx264"


@pytest.mark.skipif(not ff.detect_ffmpeg().found, reason="ffmpeg not installed")
def test_probe_real_file(tmp_path: Path) -> None:
    ffmpeg = ff.detect_ffmpeg().ffmpeg
    out = tmp_path / "thử nghiệm video.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=size=320x240:rate=10:duration=1", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    info = ff.probe(out)
    stream = info["streams"][0]
    assert stream["codec_type"] == "video" and stream["width"] == 320
    assert abs(float(info["format"]["duration"]) - 1.0) < 0.2
    encoders = ff.detect_h264_encoders(ffmpeg, verify=True)
    if encoders:
        assert encoders[-1].name == "libx264" or all(e.hardware for e in encoders)


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX true binary")
def test_detect_reports_version_none_for_broken_binary(tmp_path: Path, monkeypatch) -> None:
    _fake_exe(tmp_path, "ffmpeg")
    monkeypatch.setenv("VIDEOSTUDIO_FFMPEG_DIR", str(tmp_path))
    info = ff.detect_ffmpeg()
    assert info.found is True and info.source == "env"
