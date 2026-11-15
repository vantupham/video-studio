"""Renderer integration: really runs ffmpeg and checks the output with ffprobe.

Skipped when ffmpeg is not installed, unless ``VIDEOSTUDIO_REQUIRE_FFMPEG=1`` (set in CI)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from videostudio.media.ffmpeg import detect_ffmpeg, probe
from videostudio.pipeline.models import Overlay, Scene, Visual
from videostudio.pipeline.render_types import Branding, RenderPlan, SceneMedia
from videostudio.pipeline.renderer_ffmpeg import FFmpegRenderer
from videostudio.pipeline.styles import StyleLibrary
from videostudio.pipeline.tts_step import TTSCache, synthesize_narration
from videostudio.plugins.base import JobCancelled, JobContext
from videostudio.providers.base import ProviderContext, ResolvedProvider
from videostudio.providers.tts import MockTTS

FFMPEG = detect_ffmpeg()
if not FFMPEG.found or not FFMPEG.ffprobe:
    if os.environ.get("VIDEOSTUDIO_REQUIRE_FFMPEG") == "1":
        raise RuntimeError("ffmpeg/ffprobe required for renderer tests")
    pytest.skip("ffmpeg not installed", allow_module_level=True)


def _ctx(logs: list[str] | None = None, cancel_after: int | None = None) -> JobContext:
    progress: list[int] = []
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return cancel_after is not None and calls["n"] > cancel_after

    c = JobContext(job_id="r", project_dir=None, progress_cb=lambda p, m: progress.append(p),
                   log_cb=lambda m, level: (logs if logs is not None else []).append(m),
                   cancelled_cb=cancelled)
    c.progress_values = progress  # type: ignore[attr-defined]
    return c


async def _scenes(tmp: Path, image: Path | None = None) -> list[SceneMedia]:
    tts = ResolvedProvider(provider=MockTTS(), ctx=ProviderContext(config={
        "words_per_second": 5}), instance_id=None, instance_name="mock")
    cache = TTSCache(tmp / "tts")
    scenes = [
        Scene(id="s1", narration="Xin chào các bạn.",
              visual=Visual(type="title_card", title="Quang hợp", subtitle="Sinh học")),
        Scene(id="s2", narration="Cây xanh tạo ra ô-xi.",
              visual=Visual(type="text_card", title="Ý chính", bullets=["Ánh sáng", "Nước"]),
              overlays=[Overlay(text="Ghi nhớ!", position="top")]),
        Scene(id="s3", narration="", visual=Visual(type="image", image="x", title="Ảnh"),
              duration=1.0),
    ]
    out = []
    for scene in scenes:
        if scene.narration:
            r = await synthesize_narration(tts, scene.narration, "vi", cache)
            out.append(SceneMedia(scene=scene, audio_path=r.audio_path,
                                  audio_duration_s=r.duration_s, words=r.words))
        else:
            out.append(SceneMedia(scene=scene, image_path=image))
    return out


def _plan(tmp: Path, style_id: str, scenes: list[SceneMedia], **kw) -> RenderPlan:
    style = StyleLibrary().get(style_id)
    return RenderPlan(title="Kiểm thử dựng hình", style_id=style.id, style_data=style.data,
                      style_dirs=style.dirs, aspect=style.aspect, scenes=scenes,
                      work_dir=tmp / "work", out_dir=tmp / "Đầu ra", basename=f"thử_{style_id}",
                      **kw)


@pytest.mark.parametrize(("style_id", "size"), [("explainer_16x9", (1920, 1080)),
                                                ("short_9x16", (1080, 1920))])
async def test_render_both_styles(tmp_path, style_id, size):
    image = tmp_path / "ảnh.png"
    Image.new("RGB", (800, 600), (200, 50, 50)).save(image)
    scenes = await _scenes(tmp_path, image)
    logs: list[str] = []
    ctx = _ctx(logs)
    result = await FFmpegRenderer().render(_plan(tmp_path, style_id, scenes), ctx)

    assert result.video_path.is_file() and (result.width, result.height) == size
    info = probe(result.video_path)
    streams = {s["codec_type"]: s for s in info["streams"]}
    assert streams["video"]["codec_name"] == "h264"
    assert (streams["video"]["width"], streams["video"]["height"]) == size
    assert streams["video"]["pix_fmt"] == "yuv420p"
    assert streams["audio"]["codec_name"] == "aac"
    expected = sum(m.duration_s for m in scenes)
    assert 2.5 <= expected <= 8
    assert float(info["format"]["duration"]) == pytest.approx(expected, abs=0.25)
    for ext in ("srt", "vtt", "ass"):
        assert result.subtitles[ext].is_file()
    srt = result.subtitles["srt"].read_text(encoding="utf-8-sig")
    assert "Xin chào các bạn." in srt
    assert result.thumbnail_path and result.thumbnail_path.is_file()
    assert ctx.progress_values[-1] == 100  # type: ignore[attr-defined]
    assert any("encoder" in m for m in logs)


async def test_segment_cache_only_rerenders_changed_scene(tmp_path):
    scenes = await _scenes(tmp_path)
    await FFmpegRenderer().render(_plan(tmp_path, "explainer_16x9", scenes, preview=True), _ctx())
    segs = set((tmp_path / "work" / "segments").glob("*.mp4"))
    assert len(segs) == 3
    scenes[1].scene.visual.title = "Ý chính (đã sửa)"
    logs: list[str] = []
    await FFmpegRenderer().render(_plan(tmp_path, "explainer_16x9", scenes, preview=True),
                                  _ctx(logs))
    after = set((tmp_path / "work" / "segments").glob("*.mp4"))
    assert len(after - segs) == 1  # only the edited scene was encoded again
    assert sum("dựng hình" in m for m in logs) == 1


async def test_music_ducking_logo_and_branding(tmp_path):
    from videostudio.media.audio import write_tone_wav

    music = tmp_path / "nhạc.wav"
    write_tone_wav(music, 1.0, freq=440, volume=0.3)
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 255, 255, 200)).save(logo)
    scenes = (await _scenes(tmp_path))[:1]
    plan = _plan(tmp_path, "short_9x16", scenes, preview=True, music_path=music,
                 branding=Branding(logo_path=logo, intro_text="Kênh Học Vui",
                                   outro_text="Cảm ơn!", card_seconds=1.0))
    result = await FFmpegRenderer().render(plan, _ctx())
    info = probe(result.video_path)
    assert (result.width, result.height) == (540, 960)
    expected = 1.0 + scenes[0].duration_s + 1.0
    assert float(info["format"]["duration"]) == pytest.approx(expected, abs=0.25)


@pytest.mark.parametrize("cancel_after", [0, 1])
async def test_cancel_stops_render(tmp_path, cancel_after):
    # 0: before any work; 1: while the first ffmpeg process runs (process is killed)
    scenes = await _scenes(tmp_path)
    with pytest.raises(JobCancelled):
        await FFmpegRenderer().render(_plan(tmp_path, "explainer_16x9", scenes),
                                      _ctx(cancel_after=cancel_after))
    assert not list((tmp_path / "Đầu ra").glob("*.mp4"))
    assert not list((tmp_path / "work" / "segments").glob("*.mp4"))


def _frame_center(video: Path, t: float, rel: tuple[float, float] = (0.5, 0.5)
                  ) -> tuple[int, int, int]:
    import subprocess

    out = video.parent / f"f{t:.2f}.png"
    subprocess.run([FFMPEG.ffmpeg, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
                    "-frames:v", "1", str(out)], check=True)
    with Image.open(out) as im:
        rgb = im.convert("RGB")
        return rgb.getpixel((int(rgb.width * rel[0]), int(rgb.height * rel[1])))


@pytest.mark.parametrize("transition", ["crossfade", "fade", "none", "slideleft"])
async def test_transitions_keep_timing_and_blend(tmp_path, transition):
    white, red = tmp_path / "trắng.png", tmp_path / "đỏ.png"
    Image.new("RGB", (640, 360), (255, 255, 255)).save(white)
    Image.new("RGB", (640, 360), (220, 0, 0)).save(red)
    scenes = [SceneMedia(scene=Scene(id=f"s{i}", narration="", duration=1.0,
                                     visual=Visual(type="image", image="x")), image_path=img)
              for i, img in enumerate((white, red, white), 1)]
    plan = _plan(tmp_path, "explainer_16x9", scenes, preview=True)
    plan.style_data = {**plan.style_data, "transitions": {"type": transition, "duration_s": 0.4},
                       "ken_burns": {"enabled": False}}
    result = await FFmpegRenderer().render(plan, _ctx())
    assert result.transition == transition
    assert [(t["scene_id"], t["start"], t["end"]) for t in result.timeline] == [
        ("s1", 0.0, 1.0), ("s2", 1.0, 2.0), ("s3", 2.0, 3.0)]
    assert float(probe(result.video_path)["format"]["duration"]) == pytest.approx(3.0, abs=0.1)
    # Centre pixel shortly after the first cut: dark when fading through black, a
    # white/red mix while cross-fading, already red with a hard cut.
    r, g, b = _frame_center(result.video_path, 1.04)
    if transition == "fade":
        assert max(r, g, b) < 90
    elif transition == "crossfade":
        assert r > 200 and 120 < g < 250
    elif transition == "none":
        assert r > 180 and g < 60
    # Well inside scene 2 every variant shows plain red.
    r, g, b = _frame_center(result.video_path, 1.6)
    assert r > 180 and g < 60


async def test_ken_burns_moves_image_and_is_cached_per_mode(tmp_path):
    from videostudio.pipeline.renderer_ffmpeg import ken_burns_filter

    assert "zoompan" in ken_burns_filter("pan_left", 1.2, 30, 640, 360, 30)
    img = tmp_path / "ảnh.png"
    im = Image.new("RGB", (640, 360))
    for x in range(640):
        for y in range(360):
            im.putpixel((x, y), (x * 255 // 639, 128, 255 - x * 255 // 639))
    im.save(img)
    scenes = [SceneMedia(scene=Scene(id="s1", narration="", duration=1.5,
                                     visual=Visual(type="image", image="x")), image_path=img)]
    plan = _plan(tmp_path, "explainer_16x9", scenes, preview=True)
    plan.style_data = {**plan.style_data, "ken_burns": {"enabled": True, "zoom": 1.3,
                                                        "mode": "zoom_in"}}
    logs: list[str] = []
    result = await FFmpegRenderer().render(plan, _ctx(logs))
    assert any("Ken Burns: zoom_in" in m for m in logs)
    first = _frame_center(result.video_path, 0.3, (0.3, 0.5))
    last = _frame_center(result.video_path, 1.3, (0.3, 0.5))
    assert result.duration_s == pytest.approx(1.5, abs=0.1)
    assert abs(first[0] - last[0]) + abs(first[2] - last[2]) > 12  # the picture moved
    segs = set((tmp_path / "work" / "segments").glob("*.mp4"))
    plan.style_data["ken_burns"] = {"enabled": True, "zoom": 1.3, "mode": "pan_right"}
    await FFmpegRenderer().render(plan, _ctx())
    assert len(set((tmp_path / "work" / "segments").glob("*.mp4")) - segs) == 1
