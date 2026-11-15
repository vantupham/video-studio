"""Word tokenisation and timing utilities for Vietnamese (and other space-separated
languages). A Vietnamese "word" token here is a syllable separated by whitespace."""

from __future__ import annotations

import difflib
import re
import unicodedata

from ..base import WordTiming

_STRIP = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t for t in unicodedata.normalize("NFC", text).split() if t]


def norm_token(token: str) -> str:
    return _STRIP.sub("", unicodedata.normalize("NFC", token)).casefold()


def _weight(token: str) -> float:
    letters = sum(ch.isalnum() for ch in token)
    weight = 0.6 + 0.12 * letters  # syllable base cost + a little per character
    if token.endswith((",", ";", ":")):
        weight += 0.35
    elif token.endswith((".", "!", "?", "…")):
        weight += 0.7
    return weight


def proportional_timings(text: str, duration_s: float, *, lead_s: float = 0.05,
                         tail_s: float = 0.1) -> list[WordTiming]:
    tokens = tokenize(text)
    if not tokens or duration_s <= 0:
        return []
    lead = min(lead_s, duration_s * 0.1)
    usable = max(0.01, duration_s - lead - min(tail_s, duration_s * 0.1))
    weights = [_weight(t) for t in tokens]
    total = sum(weights)
    out: list[WordTiming] = []
    t = lead
    for token, w in zip(tokens, weights, strict=True):
        span = usable * w / total
        pause = 0.0
        if token.endswith((",", ";", ":", ".", "!", "?", "…")):
            pause = min(span * 0.35, 0.4)
        out.append(WordTiming(text=token, start=round(t, 3), end=round(t + span - pause, 3)))
        t += span
    return out


def snap_to_text(words: list[WordTiming], text: str, duration_s: float) -> list[WordTiming]:
    """Map provider/recogniser word timings onto the original tokens of ``text`` (so the
    subtitles keep the script's spelling and punctuation). Unmatched tokens are
    interpolated; the result is monotonic and inside ``[0, duration_s]``."""
    tokens = tokenize(text)
    if not tokens:
        return []
    if not words:
        return proportional_timings(text, duration_s)
    a = [norm_token(t) for t in tokens]
    b = [norm_token(w.text) for w in words]
    starts: list[float | None] = [None] * len(tokens)
    ends: list[float | None] = [None] * len(tokens)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                starts[i1 + k] = words[j1 + k].start
                ends[i1 + k] = words[j1 + k].end
        elif tag == "replace" and j2 > j1:
            # spread the replaced span's time across the original tokens
            s, e = words[j1].start, words[j2 - 1].end
            n = i2 - i1
            for k in range(n):
                starts[i1 + k] = s + (e - s) * k / n
                ends[i1 + k] = s + (e - s) * (k + 1) / n
    # interpolate gaps
    known = [i for i, s in enumerate(starts) if s is not None]
    if not known:
        return proportional_timings(text, duration_s)
    for i in range(len(tokens)):
        if starts[i] is not None:
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        lo = ends[prev] if prev is not None else 0.0
        hi = starts[nxt] if nxt is not None else duration_s
        lo_i = prev if prev is not None else -1
        hi_i = nxt if nxt is not None else len(tokens)
        frac0 = (i - lo_i - 1) / max(1, hi_i - lo_i - 1)
        frac1 = (i - lo_i) / max(1, hi_i - lo_i - 1)
        starts[i] = lo + (hi - lo) * frac0
        ends[i] = lo + (hi - lo) * frac1
    out: list[WordTiming] = []
    last = 0.0
    for token, s, e in zip(tokens, starts, ends, strict=True):
        s = min(max(float(s), last), duration_s)
        e = min(max(float(e), s + 0.01), max(duration_s, s + 0.01))
        out.append(WordTiming(text=token, start=round(s, 3), end=round(e, 3)))
        last = s
    return out


def group_chars_to_words(chars: list[str], starts: list[float], ends: list[float]) \
        -> list[WordTiming]:
    """ElevenLabs-style character alignment → word timings."""
    words: list[WordTiming] = []
    buf, s0, e0 = "", None, 0.0
    for ch, s, e in zip(chars, starts, ends, strict=False):
        if ch.isspace():
            if buf:
                words.append(WordTiming(text=buf, start=s0 or 0.0, end=e0))
            buf, s0 = "", None
            continue
        if s0 is None:
            s0 = s
        buf += ch
        e0 = e
    if buf:
        words.append(WordTiming(text=buf, start=s0 or 0.0, end=e0))
    return words
