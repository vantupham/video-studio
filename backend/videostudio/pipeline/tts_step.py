"""Narration synthesis with a content-addressed cache.

Cache key = sha256(provider type, voice, audio-affecting settings, language, text). The
audio file and its word timings are stored in ``<data dir>/cache/tts/``; re-rendering a
storyboard only synthesises scenes whose narration (or voice/provider) changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videostudio.providers.alignment.text import proportional_timings, snap_to_text
from videostudio.providers.base import ResolvedProvider, TTSProvider, WordTiming
from videostudio.providers.errors import ProviderError

log = logging.getLogger(__name__)


@dataclass
class NarrationResult:
    key: str
    audio_path: Path
    duration_s: float
    words: list[WordTiming]
    cached: bool
    timing_source: str  # tts | <alignment provider id> | proportional


def tts_cache_key(tts: ResolvedProvider, text: str, lang: str) -> str:
    provider = tts.provider
    assert isinstance(provider, TTSProvider)
    payload = {
        "provider": provider.id,
        "voice": tts.voice or provider.default_voice(tts.ctx),
        "settings": provider.cache_settings(tts.ctx),
        "lang": lang,
        "text": text,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


class TTSCache:
    def __init__(self, root: Path):
        self.root = root

    def _meta(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> NarrationResult | None:
        meta_path = self._meta(key)
        if not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            audio = meta_path.parent / meta["audio"]
            if not audio.is_file() or audio.stat().st_size == 0:
                return None
            return NarrationResult(key=key, audio_path=audio, duration_s=float(meta["duration_s"]),
                                   words=[WordTiming(**w) for w in meta["words"]], cached=True,
                                   timing_source=meta.get("timing_source", "tts"))
        except (OSError, ValueError, KeyError):
            return None

    def put(self, key: str, audio: Path, duration_s: float, words: list[WordTiming],
            timing_source: str) -> NarrationResult:
        folder = self.root / key[:2]
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{key}{audio.suffix}"
        if audio.resolve() != dest.resolve():
            shutil.move(str(audio), dest)
        meta = {"audio": dest.name, "duration_s": duration_s, "timing_source": timing_source,
                "words": [w.model_dump() for w in words]}
        self._meta(key).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return NarrationResult(key=key, audio_path=dest, duration_s=duration_s, words=words,
                               cached=False, timing_source=timing_source)


async def synthesize_narration(
    tts: ResolvedProvider, text: str, lang: str, cache: TTSCache,
    alignment: ResolvedProvider | None = None, log_cb=None,
) -> NarrationResult:
    key = tts_cache_key(tts, text, lang)
    hit = cache.get(key)
    if hit is not None:
        return hit
    provider = tts.provider
    assert isinstance(provider, TTSProvider)
    tmp_dir = cache.root / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / f"{key}{provider.audio_ext}"
    audio = await provider.synthesize(tts.ctx, text, voice=tts.voice, lang=lang, out_path=out)
    words: list[WordTiming] = list(audio.words)
    source = "tts"
    if not words and alignment is not None:
        try:
            words = await alignment.provider.align(  # type: ignore[attr-defined]
                alignment.ctx, audio_path=audio.path, text=text, lang=lang,
                duration_s=audio.duration_s)
            source = alignment.provider.id
        except ProviderError as exc:
            if log_cb:
                log_cb(f"Căn thời gian bằng {alignment.instance_name} lỗi: {exc.message}; "
                       "dùng ước lượng theo âm tiết", "warning")
            words = []
    if not words:
        words = proportional_timings(text, audio.duration_s)
        source = "proportional"
    else:
        words = snap_to_text(words, text, audio.duration_s)
    return cache.put(key, audio.path, audio.duration_s, words, source)


def narration_asset(result: NarrationResult, data_dir: Path,
                    tts: ResolvedProvider) -> dict[str, Any]:
    return {
        "tts_key": result.key,
        "audio": result.audio_path.resolve().relative_to(data_dir.resolve()).as_posix(),
        "duration_s": round(result.duration_s, 3),
        "words": [w.model_dump() for w in result.words],
        "timing_source": result.timing_source,
        "provider": tts.provider.id,
        "instance_id": tts.instance_id,
        "voice": tts.voice,
    }
