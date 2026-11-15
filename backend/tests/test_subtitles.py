"""Word timings, cue building and SRT/VTT/ASS formatting (Vietnamese)."""

from __future__ import annotations

import re

import pytest

from videostudio.pipeline.styles import StyleLibrary
from videostudio.pipeline.subtitles import (
    ass_color,
    build_cues,
    build_subtitles,
    offset_words,
    to_ass,
    to_srt,
    to_vtt,
    wrap_lines,
)
from videostudio.providers.alignment.text import (
    group_chars_to_words,
    norm_token,
    proportional_timings,
    snap_to_text,
    tokenize,
)
from videostudio.providers.base import WordTiming

TEXT = ("Xin chào các bạn, hôm nay chúng ta sẽ tìm hiểu cách biến tài liệu thành video. "
        "Quá trình này gồm ba bước: viết kịch bản, đọc thuyết minh và dựng hình!")
SRT_TS = re.compile(r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")


def _monotonic(items, attr_start="start", attr_end="end"):
    last_end = 0.0
    for it in items:
        s, e = getattr(it, attr_start), getattr(it, attr_end)
        assert s >= last_end - 1e-6, (s, last_end)
        assert e > s
        last_end = e


def test_tokenize_and_normalize_vietnamese():
    decomposed = "Vie\u0302\u0323t Nam"  # NFD
    assert tokenize(decomposed) == ["Việt", "Nam"]
    assert norm_token("“Bạn,”") == "bạn"
    assert norm_token("VIỆT!") == "việt"


def test_proportional_timings_monotonic_and_bounded():
    words = proportional_timings(TEXT, 12.0)
    assert [w.text for w in words] == TEXT.split()
    _monotonic(words)
    assert words[0].start >= 0 and words[-1].end <= 12.0
    # longer syllables / punctuation get more time
    by = {w.text: w.end - w.start for w in words}
    assert by["chúng"] > by["ta"]


def test_snap_to_text_restores_punctuation_and_fills_gaps():
    provider = [WordTiming(text="Xin", start=0.1, end=0.3),
                WordTiming(text="chào", start=0.3, end=0.6),
                # "các" missing from recogniser output
                WordTiming(text="bạn", start=0.9, end=1.2)]
    out = snap_to_text(provider, "Xin chào các bạn!", 1.5)
    assert [w.text for w in out] == ["Xin", "chào", "các", "bạn!"]
    _monotonic(out)
    assert 0.6 <= out[2].start <= 0.9
    assert snap_to_text([], "một hai", 1.0)[1].text == "hai"


def test_group_chars_to_words():
    words = group_chars_to_words(list("Ồ vậy à"), [i * 0.1 for i in range(7)],
                                 [i * 0.1 + 0.1 for i in range(7)])
    assert [w.text for w in words] == ["Ồ", "vậy", "à"]
    assert words[1].start == pytest.approx(0.2)


def test_build_cues_line_mode():
    words = proportional_timings(TEXT, 14.0)
    cues = build_cues(words, max_chars=42, max_lines=2, max_words=14)
    _monotonic(cues)
    assert " ".join(c.text for c in cues) == TEXT
    assert all(len(c.text) <= 84 for c in cues)
    # sentence ends close a cue
    assert any(c.text.endswith("video.") for c in cues)


def test_build_cues_word_mode_short():
    words = proportional_timings(TEXT, 14.0)
    cues = build_cues(words, max_chars=22, max_lines=2, max_words=3, max_duration=3.0)
    assert all(len(c.words) <= 3 for c in cues)
    _monotonic(cues)


def test_srt_and_vtt_format():
    cues = build_cues(proportional_timings(TEXT, 14.0))
    srt = to_srt(cues)
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == len(cues)
    for i, block in enumerate(blocks, 1):
        lines = block.split("\n")
        assert lines[0] == str(i)
        assert SRT_TS.match(lines[1]), lines[1]
        assert 1 <= len(lines[2:]) <= 2
    assert "Xin chào các bạn," in srt
    vtt = to_vtt(cues)
    assert vtt.startswith("WEBVTT\n") and "-->" in vtt and "," not in vtt.split("\n")[2][:12]


def test_timestamps_over_an_hour():
    cues = build_cues([WordTiming(text="muộn", start=3725.5, end=3726.25)])
    assert "01:02:05,500 --> 01:02:06,250" in to_srt(cues)
    ass = to_ass(cues, width=1920, height=1080, captions={})
    assert "Dialogue: 0,1:02:05.50,1:02:06.25" in ass


def test_wrap_lines_balanced():
    lines = wrap_lines("một hai ba bốn năm sáu bảy tám chín mười mười một", 20, 2)
    assert len(lines) == 2 and abs(len(lines[0]) - len(lines[1])) < 12


def test_ass_colors_and_escaping():
    assert ass_color("#FACC15") == "&H0015CCFA"
    assert ass_color("#000000", 0x80) == "&H80000000"
    cues = build_cues([WordTiming(text="{a}\\b", start=0, end=1)])
    ass = to_ass(cues, width=100, height=100, captions={})
    assert "{a}" not in ass.split("[Events]")[1] and "(a)/b" in ass


def test_ass_line_mode_explainer_style():
    style = StyleLibrary().get("explainer_16x9")
    words = offset_words(proportional_timings(TEXT, 14.0), 2.0)
    subs = build_subtitles(words, style.section("captions"), width=1920, height=1080,
                           title="Bài học")
    ass = subs["ass"]
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
    assert "Style: Default,Be Vietnam Pro SemiBold," in ass
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert events and all(r"\c" not in e for e in events)
    assert subs["srt"].startswith("1\n00:00:02")


def test_ass_word_highlight_for_shorts():
    style = StyleLibrary().get("short_9x16")
    captions = style.section("captions")
    words = proportional_timings("Bạn có biết điều này không?", 3.0)
    subs = build_subtitles(words, captions, width=1080, height=1920, title="Short")
    events = [ln for ln in subs["ass"].splitlines() if ln.startswith("Dialogue:")]
    # one event per word, exactly one highlighted word per event, uppercase
    assert len(events) == len(words)
    highlight = ass_color(captions["highlight"])
    for e in events:
        assert e.count(r"{\c" + highlight) == 1
    assert "BẠN" in events[0] and "KHÔNG?" in events[-1]
    times = [(e.split(",")[1], e.split(",")[2]) for e in events]
    assert times == sorted(times)
    # SRT for shorts stays readable (normal case, longer cues)
    assert "Bạn có biết điều này không?" in subs["srt"]
