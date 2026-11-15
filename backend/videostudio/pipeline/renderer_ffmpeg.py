"""FFmpeg renderer.

1. Each scene → a silent H.264 segment from a Pillow-drawn frame (cached by content hash in
   ``work_dir/segments`` so editing one scene only re-encodes that scene).
2. Each scene's narration → a WAV padded/trimmed to the exact scene length (cached).
3. Segments and WAVs are joined with the concat demuxer (stream copy).
4. Final pass: burn the ASS subtitles with libass (bundled fonts via ``fontsdir``), overlay
   the logo, duck the background music under the narration, encode with a hardware
   H.264 encoder when available (fallback libx264).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from PIL.Image import LANCZOS

from videostudio.media.ffmpeg import (
    SOFTWARE_H264,
    VideoEncoder,
    detect_h264_encoders,
    probe,
    require_ffmpeg,
)
from videostudio.media.runner import FFmpegCancelled, FFmpegError, run_ffmpeg
from videostudio.plugins.base import JobCancelled, JobContext
from videostudio.providers.base import WordTiming

from .cards import render_card
from .models import Scene, Visual
from .render_types import RenderPlan, RenderResult, SceneMedia
from .styles import FONTS_DIR, StylePack
from .subtitles import build_subtitles, offset_words

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
NARRATION_LEAD_S = 0.12
RENDERER_VERSION = "2"
# xfade with more inputs gets slow and the command line long; fall back to plain fades.
MAX_XFADE_SCENES = 80
KB_OVERSAMPLE = 1.5
KEN_BURNS_CYCLE = ("zoom_in", "pan_right", "zoom_out", "pan_left")
XFADE_ALIASES = {"cross": "crossfade", "crossfade": "crossfade", "xfade": "crossfade",
                 "cut": "none", "off": "none", "fade_black": "fadeblack"}
XFADE_TRANSITIONS = {"crossfade", "fadeblack", "fadewhite", "dissolve", "slideleft",
                     "slideright", "slideup", "slidedown", "wipeleft", "wiperight", "wipeup",
                     "wipedown", "smoothleft", "smoothright", "circleopen", "circleclose",
                     "radial", "pixelize", "distance", "hblur", "zoomin"}


def ken_burns_filter(mode: str, zoom: float, frames: int, width: int, height: int,
                     fps: int) -> str:
    """zoompan expression for one still image; ``on`` is the output frame number."""
    last = max(1, frames - 1)
    progress = f"(on/{last})"
    center_x, center_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if mode == "zoom_out":
        z, x, y = f"{zoom:.4f}-{zoom - 1:.4f}*{progress}", center_x, center_y
    elif mode == "pan_right":
        z, x, y = f"{zoom:.4f}", f"(iw-iw/zoom)*{progress}", center_y
    elif mode == "pan_left":
        z, x, y = f"{zoom:.4f}", f"(iw-iw/zoom)*(1-{progress})", center_y
    else:  # zoom_in
        z, x, y = f"1+{zoom - 1:.4f}*{progress}", center_x, center_y
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"


def _hash(data: Any) -> str:
    blob = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _file_sig(path: Path | None) -> list[Any] | None:
    if path is None or not path.is_file():
        return None
    st = path.stat()
    return [str(path), st.st_size, int(st.st_mtime)]


def encoder_args(enc: VideoEncoder, preview: bool) -> list[str]:
    if enc.name == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
    if enc.name == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "23"]
    if enc.name == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "21",
                "-qp_p", "23"]
    return ["-c:v", "libx264", "-preset", "veryfast" if not preview else "ultrafast",
            "-crf", "21" if not preview else "26", "-pix_fmt", "yuv420p", "-profile:v", "high"]


class FFmpegRenderer:
    id = "ffmpeg"
    name = "FFmpeg (tích hợp)"

    def config_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {
            "prefer_hardware": {"type": "boolean", "title": "Ưu tiên mã hoá bằng GPU",
                                "default": True}}}

    # ------------------------------------------------------------------ timeline

    @staticmethod
    def scene_duration(media: SceneMedia, style: StylePack, fps: int) -> float:
        pacing = style.section("pacing")
        if media.scene.duration != "auto":
            d = float(media.scene.duration)
        elif media.audio_path and media.audio_duration_s > 0:
            d = media.audio_duration_s + NARRATION_LEAD_S + float(
                pacing.get("scene_padding_s", 0.4))
        else:
            d = float(pacing.get("min_scene_s", 2.0))
        d = max(d, float(pacing.get("min_scene_s", 2.0)) if media.scene.duration == "auto"
                else 0.5)
        frames = max(1, round(d * fps))
        return frames / fps

    @staticmethod
    def _branding_scenes(plan: RenderPlan) -> tuple[list[SceneMedia], list[SceneMedia]]:
        b = plan.branding
        intro = [SceneMedia(scene=Scene(id="intro", visual=Visual(type="title_card",
                                                                  title=b.intro_text),
                                        duration=b.card_seconds))] if b.intro_text else []
        outro = [SceneMedia(scene=Scene(id="outro", visual=Visual(type="title_card",
                                                                  title=b.outro_text),
                                        duration=b.card_seconds))] if b.outro_text else []
        return intro, outro

    # ------------------------------------------------------------------ render

    async def render(self, plan: RenderPlan, ctx: JobContext) -> RenderResult:
        info = require_ffmpeg()
        ffmpeg = info.ffmpeg
        assert ffmpeg
        style = StylePack(id=plan.style_id, data=plan.style_data, dirs=plan.style_dirs)
        width, height = style.resolution(plan.aspect, plan.preview)
        fps = style.fps if not plan.preview else min(style.fps, 24)
        fade = float(style.section("pacing").get("fade_s", 0.25))
        work = plan.work_dir
        for sub in ("segments", "cards", "audio", "fonts"):
            (work / sub).mkdir(parents=True, exist_ok=True)
        plan.out_dir.mkdir(parents=True, exist_ok=True)
        intro, outro = self._branding_scenes(plan)
        timeline = intro + list(plan.scenes) + outro
        for media in timeline:
            media.duration_s = self.scene_duration(media, style, fps)
        total = sum(m.duration_s for m in timeline)
        cancelled = ctx.is_cancelled

        def progress(lo: float, hi: float, msg: str):
            def cb(frac: float) -> None:
                ctx.report_progress(lo + (hi - lo) * frac, msg)
            return cb

        transition, trans_d = self.transition(style)
        n = len(timeline)
        # Crossfade-style transitions overlap into the *next* scene: each segment except
        # the last is rendered ``tail`` seconds longer and blended over the next one, so
        # scene start times (and the narration track) are unchanged.
        tails = [0.0] * n
        if transition not in ("none", "fade") and n > 1 and n <= MAX_XFADE_SCENES:
            for i in range(n - 1):
                t = min(trans_d, 0.4 * timeline[i].duration_s, 0.4 * timeline[i + 1].duration_s)
                tails[i] = round(t * fps) / fps
        else:
            transition = "fade" if transition != "none" else "none"
        ken = style.section("ken_burns")
        ken_on = bool(ken.get("enabled", True))
        ken_zoom = max(1.0, min(1.5, float(ken.get("zoom", 1.12))))
        ken_mode = str(ken.get("mode", "alternate"))
        timeline_out: list[dict[str, Any]] = []
        t_start = 0.0
        card_kinds = {id(m): "intro" for m in intro} | {id(m): "outro" for m in outro}
        for media in timeline:
            kind = card_kinds.get(id(media), "scene")
            timeline_out.append({"scene_id": media.scene.id, "kind": kind,
                                 "start": round(t_start, 3),
                                 "end": round(t_start + media.duration_s, 3)})
            t_start += media.duration_s

        try:
            # 1. video segments ------------------------------------------------
            segments: list[Path] = []
            style_sig = _hash([style.section("colors"), style.section("cards"),
                               style.section("fonts"), RENDERER_VERSION])
            image_no = 0
            for i, media in enumerate(timeline):
                ctx.check_cancelled()
                is_image = media.scene.visual.type in ("image", "stock") and \
                    media.image_path is not None
                motion = None
                if is_image and ken_on:
                    motion = ken_mode if ken_mode != "alternate" else \
                        KEN_BURNS_CYCLE[image_no % len(KEN_BURNS_CYCLE)]
                    image_no += 1
                if transition == "fade":
                    f = min(fade, media.duration_s / 4)
                    fades = (f, f)
                elif transition == "none":
                    fades = (0.0, 0.0)
                else:  # xfade: only fade in the very first and out of the very last scene
                    f = min(fade, media.duration_s / 4)
                    fades = (f if i == 0 else 0.0, f if i == n - 1 else 0.0)
                seg_dur = media.duration_s + tails[i]
                sig = {"visual": media.scene.visual.model_dump(),
                       "overlays": [o.model_dump() for o in media.scene.overlays],
                       "image": _file_sig(media.image_path), "size": [width, height],
                       "fps": fps, "dur": seg_dur, "fades": fades, "style": style_sig,
                       "motion": motion, "zoom": ken_zoom if motion else None}
                key = _hash(sig)
                seg = work / "segments" / f"{key}.mp4"
                if not seg.is_file():
                    card = work / "cards" / f"{key}.png"
                    image = render_card(media.scene.visual, style, (width, height),
                                        media.scene.overlays, media.image_path,
                                        caption_safe=plan.burn_subtitles)
                    frames = max(1, round(seg_dur * fps))
                    vf = []
                    if motion:
                        # Oversample the still so zoompan's integer crop offsets don't
                        # make the motion shake.
                        image = image.resize((int(width * KB_OVERSAMPLE),
                                              int(height * KB_OVERSAMPLE)), LANCZOS)
                        vf.append(ken_burns_filter(motion, ken_zoom, frames, width, height,
                                                   fps))
                    image.save(card, "PNG", compress_level=1)
                    if fades[0] > 0:
                        vf.append(f"fade=t=in:st=0:d={fades[0]:.3f}")
                    if fades[1] > 0:
                        vf.append(f"fade=t=out:st={seg_dur - fades[1]:.3f}:d={fades[1]:.3f}")
                    vf.append("format=yuv420p")
                    tmp = seg.with_suffix(".part.mp4")
                    await run_ffmpeg(
                        [ffmpeg, "-loop", "1", "-framerate", str(fps), "-i", str(card),
                         "-frames:v", str(frames), "-vf", ",".join(vf), "-r", str(fps),
                         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14",
                         "-g", str(fps * 2), "-pix_fmt", "yuv420p", "-an", str(tmp)],
                        is_cancelled=cancelled)
                    tmp.replace(seg)
                    ctx.log(f"Cảnh {media.scene.id}: dựng hình {media.duration_s:.1f}s"
                            + (f" (Ken Burns: {motion})" if motion else ""))
                segments.append(seg)
                ctx.report_progress(5 + 50 * (i + 1) / n, f"Dựng hình {i + 1}/{n}")

            # 2. narration audio -------------------------------------------------
            wavs: list[Path] = []
            for media in timeline:
                ctx.check_cancelled()
                key = _hash({"audio": _file_sig(media.audio_path), "dur": media.duration_s,
                             "lead": NARRATION_LEAD_S, "v": RENDERER_VERSION})
                wav = work / "audio" / f"{key}.wav"
                if not wav.is_file():
                    tmp = wav.with_suffix(".part.wav")
                    if media.audio_path and media.audio_path.is_file():
                        lead_ms = int(NARRATION_LEAD_S * 1000)
                        args = [ffmpeg, "-i", str(media.audio_path), "-af",
                                f"aresample={SAMPLE_RATE},aformat=sample_fmts=s16:"
                                f"channel_layouts=stereo,adelay={lead_ms}|{lead_ms},apad,"
                                f"atrim=0:{media.duration_s:.4f}"]
                    else:
                        args = [ffmpeg, "-f", "lavfi", "-i",
                                f"anullsrc=r={SAMPLE_RATE}:cl=stereo", "-t",
                                f"{media.duration_s:.4f}"]
                    await run_ffmpeg([*args, "-ar", str(SAMPLE_RATE), "-ac", "2",
                                      "-c:a", "pcm_s16le", str(tmp)], is_cancelled=cancelled)
                    tmp.replace(wav)
                wavs.append(wav)
            ctx.report_progress(58, "Ghép âm thanh")

            seg_list = work / "segments.txt"
            seg_list.write_text("".join(f"file '{p.relative_to(work).as_posix()}'\n"
                                        for p in segments), encoding="utf-8")
            wav_list = work / "audio.txt"
            wav_list.write_text("".join(f"file '{p.relative_to(work).as_posix()}'\n"
                                        for p in wavs), encoding="utf-8")
            if any(tails):
                await self._xfade_join(ffmpeg, segments, timeline, tails, transition, fps,
                                       total, work, cancelled)
            else:
                await run_ffmpeg([ffmpeg, "-f", "concat", "-safe", "0", "-i", "segments.txt",
                                  "-c", "copy", "video_only.mp4"], cwd=work,
                                 is_cancelled=cancelled)
            await run_ffmpeg([ffmpeg, "-f", "concat", "-safe", "0", "-i", "audio.txt",
                              "-c", "copy", "narration.wav"], cwd=work, is_cancelled=cancelled)
            ctx.report_progress(62, "Tạo phụ đề")

            # 3. subtitles ---------------------------------------------------------
            words: list[WordTiming] = []
            t = 0.0
            for media in timeline:
                if media.words:
                    words.extend(offset_words(media.words, t + NARRATION_LEAD_S))
                t += media.duration_s
            captions = style.section("captions")
            subs = build_subtitles(words, captions, width=width, height=height, title=plan.title)
            outputs: dict[str, Path] = {}
            for ext in ("srt", "vtt", "ass"):
                path = plan.out_dir / f"{plan.basename}.{ext}"
                path.write_text(subs[ext], encoding="utf-8-sig" if ext == "srt" else "utf-8")
                outputs[ext] = path
            (work / "subs.ass").write_text(subs["ass"], encoding="utf-8")
            fonts_dir = work / "fonts"
            for src_dir in [FONTS_DIR, *[d / "assets" for d in style.dirs]]:
                if src_dir.is_dir():
                    for font in src_dir.glob("*.[ot]tf"):
                        dest = fonts_dir / font.name
                        if not dest.is_file() or dest.stat().st_size != font.stat().st_size:
                            shutil.copyfile(font, dest)

            # 4. final encode ---------------------------------------------------------
            out_video = plan.out_dir / f"{plan.basename}.mp4"
            encoders = list(detect_h264_encoders(ffmpeg))
            prefer_hw = bool(plan.options.get("prefer_hardware", True)) and not plan.preview
            chain = [e for e in encoders if e.hardware] if prefer_hw else []
            chain.append(SOFTWARE_H264)
            used = None
            last_error: FFmpegError | None = None
            for enc in chain:
                args = self._final_args(ffmpeg, plan, enc, width, height, total, words_present=bool(
                    words) and plan.burn_subtitles)
                tmp = out_video.with_suffix(".part.mp4")
                try:
                    await run_ffmpeg([*args, str(tmp)], cwd=work, total_s=total,
                                     on_progress=progress(65, 97, "Xuất video"),
                                     is_cancelled=cancelled)
                except FFmpegError as exc:
                    last_error = exc
                    ctx.log(f"Encoder {enc.name} lỗi, thử encoder khác: {exc}", "warning")
                    continue
                tmp.replace(out_video)
                used = enc
                break
            if used is None:
                raise last_error or FFmpegError("Không xuất được video")
            ctx.log(f"Đã xuất video bằng encoder {used.name}")

            thumb = plan.out_dir / f"{plan.basename}.jpg"
            try:
                await run_ffmpeg([ffmpeg, "-ss", f"{min(1.0, total / 2):.2f}", "-i",
                                  str(out_video), "-frames:v", "1", "-vf", "scale=640:-2",
                                  str(thumb)], is_cancelled=cancelled)
            except FFmpegError:
                thumb = None  # type: ignore[assignment]
        except FFmpegCancelled as exc:
            raise JobCancelled() from exc

        meta = probe(out_video, info.ffprobe)
        duration = float(meta.get("format", {}).get("duration") or total)
        ctx.report_progress(100, "Hoàn tất")
        return RenderResult(video_path=out_video, duration_s=duration, width=width,
                            height=height, encoder=used.name,
                            subtitles={k: v for k, v in outputs.items()},
                            thumbnail_path=thumb if thumb and thumb.is_file() else None,
                            timeline=timeline_out, transition=transition)

    @staticmethod
    def transition(style: StylePack) -> tuple[str, float]:
        cfg = style.section("transitions")
        kind = str(cfg.get("type", "fade") or "none").strip().lower()
        kind = XFADE_ALIASES.get(kind, kind)
        if kind not in ("none", "fade") and kind not in XFADE_TRANSITIONS:
            log.warning("Unknown transition %r, using crossfade", kind)
            kind = "crossfade"
        return kind, max(0.1, min(2.0, float(cfg.get("duration_s", 0.5))))

    async def _xfade_join(self, ffmpeg: str, segments: list[Path], timeline: list[SceneMedia],
                          tails: list[float], transition: str, fps: int, total: float,
                          work: Path, cancelled) -> None:
        name = "fade" if transition == "crossfade" else transition
        args = [ffmpeg]
        for seg in segments:
            args += ["-i", seg.relative_to(work).as_posix()]
        filters = [f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS,fps={fps}[v{i}]"
                   for i in range(len(segments))]
        prev, start = "[v0]", 0.0
        for i in range(1, len(segments)):
            start += timeline[i - 1].duration_s
            out = f"[x{i}]"
            if tails[i - 1] > 0:
                filters.append(f"{prev}[v{i}]xfade=transition={name}:"
                               f"duration={tails[i - 1]:.3f}:offset={start:.3f}{out}")
            else:
                filters.append(f"{prev}[v{i}]concat=n=2:v=1:a=0{out}")
            prev = out
        script = work / "xfade.txt"
        script.write_text(";".join(filters), encoding="utf-8")
        await run_ffmpeg([*args, "-filter_complex_script", "xfade.txt", "-map", prev,
                          "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "ultrafast",
                          "-crf", "14", "-pix_fmt", "yuv420p", "-r", str(fps),
                          "video_only.mp4"], cwd=work, total_s=total, is_cancelled=cancelled)

    def _final_args(self, ffmpeg: str, plan: RenderPlan, enc: VideoEncoder, width: int,
                    height: int, total: float, words_present: bool) -> list[str]:
        args = [ffmpeg, "-i", "video_only.mp4", "-i", "narration.wav"]
        idx = 2
        music_idx = logo_idx = None
        if plan.music_path and plan.music_path.is_file():
            args += ["-stream_loop", "-1", "-i", str(plan.music_path)]
            music_idx, idx = idx, idx + 1
        logo = plan.branding.logo_path
        if logo and logo.is_file():
            args += ["-i", str(logo)]
            logo_idx, idx = idx, idx + 1
        filters = []
        v = "[0:v]"
        if words_present:
            filters.append(f"{v}subtitles=subs.ass:fontsdir=fonts[vs]")
            v = "[vs]"
        if logo_idx is not None:
            b = plan.branding
            lw = max(16, int(width * b.logo_scale))
            m = int(min(width, height) * 0.035)
            pos = {"top-left": (f"{m}", f"{m}"), "top-right": (f"W-w-{m}", f"{m}"),
                   "bottom-left": (f"{m}", f"H-h-{m}"),
                   "bottom-right": (f"W-w-{m}", f"H-h-{m}")}.get(b.logo_position,
                                                                (f"W-w-{m}", f"{m}"))
            filters.append(f"[{logo_idx}:v]scale={lw}:-1,format=rgba,"
                           f"colorchannelmixer=aa={b.logo_opacity:.2f}[lg]")
            filters.append(f"{v}[lg]overlay={pos[0]}:{pos[1]}[vl]")
            v = "[vl]"
        a = "[1:a]"
        if music_idx is not None:
            vol = max(0.0, min(1.0, plan.music_volume))
            fade_st = max(0.0, total - 2.0)
            filters.append(f"[{music_idx}:a]aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:"
                           f"channel_layouts=stereo,volume={vol:.3f},"
                           f"afade=t=out:st={fade_st:.2f}:d=2[m]")
            filters.append("[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,asplit=2[n1][n2]")
            filters.append("[m][n1]sidechaincompress=threshold=0.02:ratio=10:attack=15:"
                           "release=350[md]")
            filters.append("[n2][md]amix=inputs=2:duration=first:dropout_transition=0:"
                           "normalize=0[am]")
            a = "[am]"
        if filters:
            args += ["-filter_complex", ";".join(filters)]
        args += ["-map", v if v != "[0:v]" else "0:v", "-map", a if a != "[1:a]" else "1:a"]
        args += encoder_args(enc, plan.preview)
        args += ["-r", str(min(60, max(1, int(round(self._fps(plan)))))),
                 "-c:a", "aac", "-b:a", "160k", "-ar", str(SAMPLE_RATE),
                 "-movflags", "+faststart", "-t", f"{total:.3f}"]
        title = plan.title.replace("\n", " ")[:200]
        args += ["-metadata", f"title={title}", "-metadata", "comment=Video Studio"]
        return args

    @staticmethod
    def _fps(plan: RenderPlan) -> float:
        fps = int(plan.style_data.get("fps", 30))
        return min(fps, 24) if plan.preview else fps
