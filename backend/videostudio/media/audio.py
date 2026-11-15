"""Small audio helpers shared by TTS providers and the renderer."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from .ffmpeg import probe


def media_duration(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except (wave.Error, EOFError):
            pass
    info = probe(path)
    duration = info.get("format", {}).get("duration")
    if duration is None:
        durations = [float(s["duration"]) for s in info.get("streams", []) if s.get("duration")]
        duration = max(durations) if durations else 0.0
    return float(duration)


def write_tone_wav(
    path: Path, duration_s: float, *, sample_rate: int = 24000, freq: float = 220.0,
    volume: float = 0.15, word_spans: list[tuple[float, float]] | None = None,
) -> None:
    """Write a mono 16-bit WAV. With ``word_spans`` the tone is only audible during
    words (sounds like a 'beep' per word) — useful as a mock narration."""
    frames = max(1, int(duration_s * sample_rate))
    amp = int(32767 * volume)
    spans = word_spans or [(0.0, duration_s)]
    data = bytearray()
    span_idx = 0
    for i in range(frames):
        t = i / sample_rate
        while span_idx < len(spans) and t > spans[span_idx][1]:
            span_idx += 1
        on = span_idx < len(spans) and spans[span_idx][0] <= t <= spans[span_idx][1]
        value = int(amp * math.sin(2 * math.pi * freq * t)) if on else 0
        data += struct.pack("<h", value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(data))
