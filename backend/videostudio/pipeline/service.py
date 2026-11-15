"""Pipeline orchestration used by the job handlers and the API."""

from __future__ import annotations

import copy
import hashlib
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from videostudio.config import Settings
from videostudio.db import AppSetting, Asset, IRDocument, Project, Render, Source, utcnow
from videostudio.db import Storyboard as StoryboardRow
from videostudio.ir import ContentIR
from videostudio.plugins.base import JobCancelled, JobContext
from videostudio.providers.base import MediaProvider, ResolvedProvider, WordTiming
from videostudio.providers.errors import ProviderError
from videostudio.providers.service import ProviderNotFound, ProviderService
from videostudio.review import lifecycle
from videostudio.textutil import safe_filename

from .models import GenerationSettings, Storyboard
from .render_types import Branding, RenderPlan, SceneMedia
from .renderer_ffmpeg import FFmpegRenderer
from .script_writer import make_image_resolver, regenerate_scene, write_script
from .styles import RESOLUTIONS, StyleLibrary
from .tts_step import TTSCache, narration_asset, synthesize_narration

log = logging.getLogger(__name__)

BRANDING_KEY = "branding"
DEFAULT_BRANDING: dict[str, Any] = {
    "enabled": False,
    "logo_asset_id": None,
    "logo_position": "top-right",
    "logo_scale": 0.12,
    "logo_opacity": 0.85,
    "intro_text": "",
    "outro_text": "",
    "card_seconds": 2.5,
}

RENDER_STEPS = [
    {"id": "tts", "label": "Giọng đọc", "start": 0, "end": 35},
    {"id": "media", "label": "Hình minh hoạ", "start": 35, "end": 40},
    {"id": "render", "label": "Dựng hình", "start": 40, "end": 80},
    {"id": "subtitles", "label": "Phụ đề", "start": 80, "end": 82},
    {"id": "encode", "label": "Xuất video", "start": 82, "end": 100},
]
SCRIPT_STEPS = [
    {"id": "script", "label": "Viết kịch bản", "start": 0, "end": 85},
    {"id": "storyboard", "label": "Dựng storyboard", "start": 85, "end": 100},
]


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def _sub_ctx(ctx: JobContext, lo: float, hi: float, prefix: str = "") -> JobContext:
    def cb(pct: int, msg: str | None) -> None:
        ctx.report_progress(lo + (hi - lo) * pct / 100, f"{prefix}{msg}" if msg else prefix)

    return replace(ctx, progress_cb=cb)


class PipelineService:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session],
                 providers: ProviderService, styles: StyleLibrary | None = None):
        self.settings = settings
        self.session_factory = session_factory
        self.providers = providers
        self.styles = styles or StyleLibrary(settings.data_dir / "styles")
        self.renderer = FFmpegRenderer()
        self.tts_cache = TTSCache(settings.data_dir / "cache" / "tts")

    # ------------------------------------------------------------------ settings

    def branding(self) -> dict[str, Any]:
        with self.session_factory() as s:
            row = s.get(AppSetting, BRANDING_KEY)
            return _merge(DEFAULT_BRANDING, row.value if row else {})

    def set_branding(self, value: dict[str, Any]) -> dict[str, Any]:
        merged = _merge(DEFAULT_BRANDING, {k: v for k, v in value.items()
                                           if k in DEFAULT_BRANDING})
        if merged.get("logo_asset_id") is None:
            merged["logo_asset_id"] = None
        if "logo_asset_id" in value and value["logo_asset_id"] in ("", None):
            merged["logo_asset_id"] = None
        with self.session_factory.begin() as s:
            row = s.get(AppSetting, BRANDING_KEY)
            if row is None:
                s.add(AppSetting(key=BRANDING_KEY, value=merged))
            else:
                row.value = merged
        return merged

    def project_settings(self, project_id: str) -> dict[str, Any]:
        with self.session_factory() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise KeyError(project_id)
            return dict(project.settings or {})

    def set_project_settings(self, project_id: str, value: dict[str, Any]) -> dict[str, Any]:
        # validate by building effective settings
        GenerationSettings.model_validate(_merge({}, value))
        with self.session_factory.begin() as s:
            project = s.get(Project, project_id)
            if project is None:
                raise KeyError(project_id)
            project.settings = value
        return value

    def effective_settings(self, project_id: str, overrides: dict[str, Any] | None
                           ) -> GenerationSettings:
        return GenerationSettings.model_validate(
            _merge(self.project_settings(project_id), overrides or {}))

    def _resolve(self, capability: str, gen: GenerationSettings, project: dict[str, Any]
                 ) -> ResolvedProvider:
        choice = getattr(gen, capability).model_dump()
        return self.providers.resolve(capability, choice, project.get(capability))

    def _resolve_optional(self, capability: str, gen: GenerationSettings,
                          project: dict[str, Any]) -> ResolvedProvider | None:
        try:
            return self._resolve(capability, gen, project)
        except ProviderNotFound:
            return None

    # ------------------------------------------------------------------ storyboards

    def _asset_path(self, asset_id: str | None) -> Path | None:
        if not asset_id:
            return None
        with self.session_factory() as s:
            asset = s.get(Asset, asset_id)
            return self.settings.data_dir / asset.rel_path if asset else None

    async def create_storyboard(self, project_id: str, document_id: str | None,
                                overrides: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        gen = self.effective_settings(project_id, overrides)
        project_settings = self.project_settings(project_id)
        style = self.styles.get(gen.style)
        with self.session_factory() as s:
            stmt = select(IRDocument).where(IRDocument.project_id == project_id)
            if document_id:
                stmt = stmt.where(IRDocument.id == document_id)
            doc = s.scalars(stmt.order_by(IRDocument.created_at.desc())).first()
            if doc is None:
                raise ValueError("Chưa có tài liệu đã xử lý. Hãy thêm nguồn và bấm 'Xử lý'.")
            ir = ContentIR.model_validate(doc.data)
            doc_id = doc.id
            source_dirs = []
            if doc.source_id:
                src = s.get(Source, doc.source_id)
                if src:
                    source_dirs.append((self.settings.data_dir / src.rel_path).parent)
        llm = None if gen.use_as_is else self._resolve("llm", gen, project_settings)
        if llm:
            ctx.log(f"Viết kịch bản bằng {llm.instance_name} ({llm.model or 'model mặc định'})")
        else:
            ctx.log("Giữ nguyên văn bản, chỉ chia cảnh")
        ctx.report_progress(5, "Viết kịch bản")
        resolver = make_image_resolver(self.settings.data_dir, source_dirs)
        storyboard = await write_script(llm, style, ir, gen, resolver)
        ctx.check_cancelled()
        ctx.report_progress(85, "Dựng storyboard")
        resolved = gen.model_dump()
        for cap in ("llm", "tts", "alignment", "media"):
            r = self._resolve_optional(cap, gen, project_settings)
            if r:
                resolved[cap] = {"instance_id": r.instance_id, "model": r.model, "voice": r.voice}
        resolved["aspect"] = storyboard.aspect
        with self.session_factory.begin() as s:
            row = StoryboardRow(project_id=project_id, document_id=doc_id, title=storyboard.title,
                                data=storyboard.model_dump(mode="json"), settings=resolved,
                                assets={})
            s.add(row)
            s.flush()
            sid = row.id
        ctx.log(f"Storyboard {len(storyboard.scenes)} cảnh")
        ctx.report_progress(100, "Xong")
        return {"storyboard_id": sid, "scenes": len(storyboard.scenes)}

    def get_storyboard(self, storyboard_id: str) -> tuple[Storyboard, StoryboardRow]:
        with self.session_factory() as s:
            row = s.get(StoryboardRow, storyboard_id)
            if row is None:
                raise KeyError(storyboard_id)
            s.expunge(row)
        return Storyboard.model_validate(row.data), row

    def update_storyboard(self, storyboard_id: str, data: dict[str, Any],
                          settings_patch: dict[str, Any] | None = None) -> StoryboardRow:
        storyboard = Storyboard.model_validate(data)
        with self.session_factory.begin() as s:
            row = s.get(StoryboardRow, storyboard_id)
            if row is None:
                raise KeyError(storyboard_id)
            old = {sc["id"]: sc for sc in row.data.get("scenes", [])}
            assets = dict(row.assets or {})
            for scene in storyboard.scenes:
                prev = old.get(scene.id)
                if prev is None or prev.get("narration") != scene.narration:
                    assets.pop(scene.id, None)
            for sid in list(assets):
                if sid not in {sc.id for sc in storyboard.scenes}:
                    assets.pop(sid)
            row.data = storyboard.model_dump(mode="json")
            row.title = storyboard.title
            row.assets = assets
            if settings_patch:
                merged = _merge(row.settings or {}, settings_patch)
                GenerationSettings.model_validate(merged)
                row.settings = merged
            s.flush()
            s.expunge(row)
            return row

    async def regenerate_scene(self, storyboard_id: str, scene_id: str, instruction: str,
                               ctx: JobContext) -> dict[str, Any]:
        storyboard, row = self.get_storyboard(storyboard_id)
        gen = GenerationSettings.model_validate(row.settings or {})
        style = self.styles.get(storyboard.style)
        llm = self._resolve("llm", gen, self.project_settings(row.project_id))
        ctx.log(f"Viết lại cảnh {scene_id} bằng {llm.instance_name}: "
                f"{instruction or '(không có yêu cầu)'}")
        ctx.report_progress(10, "Đang viết lại cảnh")
        new_scene = await regenerate_scene(llm, style, storyboard, scene_id, instruction, gen)
        data = storyboard.model_dump(mode="json")
        data["scenes"] = [new_scene.model_dump(mode="json") if sc["id"] == scene_id else sc
                          for sc in data["scenes"]]
        self.update_storyboard(storyboard_id, data)
        ctx.report_progress(100, "Xong")
        return {"storyboard_id": storyboard_id, "scene": new_scene.model_dump(mode="json")}

    # ------------------------------------------------------------------ assets

    async def synthesize_scenes(self, storyboard_id: str, ctx: JobContext,
                                only: list[str] | None = None) -> dict[str, Any]:
        storyboard, row = self.get_storyboard(storyboard_id)
        gen = GenerationSettings.model_validate(row.settings or {})
        project_settings = self.project_settings(row.project_id)
        tts = self._resolve("tts", gen, project_settings)
        alignment = self._resolve_optional("alignment", gen, project_settings)
        ctx.log(f"Giọng đọc: {tts.instance_name} / {tts.voice or 'mặc định'}")
        assets = dict(row.assets or {})
        scenes = [s for s in storyboard.scenes if not only or s.id in only]
        hits = 0
        for i, scene in enumerate(scenes):
            ctx.check_cancelled()
            text = scene.narration.strip()
            if not text:
                assets.pop(scene.id, None)
                continue
            result = await synthesize_narration(tts, text, storyboard.language, self.tts_cache,
                                                alignment, log_cb=ctx.log)
            hits += int(result.cached)
            assets[scene.id] = narration_asset(result, self.settings.data_dir, tts)
            ctx.log(f"Cảnh {scene.id}: {result.duration_s:.1f}s "
                    f"({'cache' if result.cached else 'mới'}, mốc thời gian: "
                    f"{result.timing_source})")
            ctx.report_progress(100 * (i + 1) / max(1, len(scenes)),
                                f"Giọng đọc cảnh {i + 1}/{len(scenes)}")
        with self.session_factory.begin() as s:
            db_row = s.get(StoryboardRow, storyboard_id)
            if db_row is not None:
                current = dict(db_row.assets or {})
                current.update({k: v for k, v in assets.items()})
                for scene in scenes:
                    if scene.id not in assets:
                        current.pop(scene.id, None)
                db_row.assets = current
        return {"scenes": len(scenes), "cache_hits": hits, "assets": assets}

    async def _stock_image(self, media: ResolvedProvider | None, query: str, aspect: str,
                           ctx: JobContext) -> Path | None:
        if media is None or not query:
            return None
        provider = media.provider
        assert isinstance(provider, MediaProvider)
        key = hashlib.sha256(f"{provider.id}|{query}|{aspect}".encode()).hexdigest()[:32]
        dest = self.settings.data_dir / "cache" / "media" / f"{key}.jpg"
        if dest.is_file():
            return dest
        orientation = "portrait" if aspect == "9:16" else "landscape"
        try:
            items = await provider.search(media.ctx, query, orientation=orientation, limit=3)
            if not items:
                ctx.log(f"Không tìm thấy ảnh cho '{query}'", "warning")
                return None
            return await provider.download(media.ctx, items[0], dest)
        except ProviderError as exc:
            ctx.log(f"Lấy ảnh '{query}' lỗi: {exc.message}", "warning")
            return None

    # ------------------------------------------------------------------ render

    def create_render(self, storyboard_id: str, preview: bool = False,
                      aspect: str | None = None) -> Render:
        """Queue a render row. ``aspect`` different from the storyboard's makes a *variant*
        (e.g. 9:16 for Reels) that leaves the review status alone."""
        storyboard, row = self.get_storyboard(storyboard_id)
        aspect = aspect or storyboard.aspect
        if aspect not in RESOLUTIONS:
            raise ValueError(f"Tỉ lệ khung hình không hỗ trợ: {aspect}")
        variant = aspect != storyboard.aspect
        with self.session_factory.begin() as s:
            db_row = s.get(StoryboardRow, storyboard_id)
            assert db_row is not None
            lifecycle.on_render_created(s, db_row, variant=variant, preview=preview)
            render = Render(project_id=row.project_id, storyboard_id=storyboard_id,
                            status="queued", style=storyboard.style, aspect=aspect,
                            settings={"preview": preview, "variant": variant})
            s.add(render)
            s.flush()
            s.expunge(render)
            return render

    def _set_render(self, render_id: str, **fields: Any) -> None:
        with self.session_factory.begin() as s:
            render = s.get(Render, render_id)
            if render is not None:
                for k, v in fields.items():
                    setattr(render, k, v)

    async def render(self, render_id: str, ctx: JobContext) -> dict[str, Any]:
        with self.session_factory() as s:
            render = s.get(Render, render_id)
            if render is None:
                raise KeyError(render_id)
            storyboard_id, preview = render.storyboard_id, bool(render.settings.get("preview"))
            project_id, aspect = render.project_id, render.aspect
        if storyboard_id is None:
            raise ValueError("Storyboard đã bị xoá")
        with self.session_factory.begin() as s:
            render = s.get(Render, render_id)
            assert render is not None
            render.status, render.job_id, render.error = "running", ctx.job_id, None
            row = s.get(StoryboardRow, storyboard_id)
            if row is not None:
                lifecycle.on_render_started(s, row, render)
        try:
            result = await self._render(render_id, storyboard_id, project_id, preview, ctx,
                                        aspect)
        except Exception as exc:
            status = "cancelled" if isinstance(exc, JobCancelled) else "failed"
            message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            self._finish_render(render_id, False, status=status, error=message,
                                finished_at=utcnow())
            raise
        return result

    def _finish_render(self, render_id: str, ok: bool, **fields: Any) -> None:
        with self.session_factory.begin() as s:
            render = s.get(Render, render_id)
            if render is None:
                return
            for k, v in fields.items():
                setattr(render, k, v)
            row = s.get(StoryboardRow, render.storyboard_id) if render.storyboard_id else None
            if row is not None:
                lifecycle.on_render_finished(s, row, render, ok, fields.get("error"))

    async def _render(self, render_id: str, storyboard_id: str, project_id: str,
                      preview: bool, ctx: JobContext,
                      aspect: str | None = None) -> dict[str, Any]:
        steps = {s["id"]: s for s in RENDER_STEPS}
        tts_step = steps["tts"]
        synth = await self.synthesize_scenes(
            storyboard_id, _sub_ctx(ctx, tts_step["start"], tts_step["end"]))
        storyboard, row = self.get_storyboard(storyboard_id)
        aspect = aspect or storyboard.aspect
        content = lifecycle.content_hash(row.data, row.settings)
        gen = GenerationSettings.model_validate(row.settings or {})
        project_settings = self.project_settings(project_id)
        style = self.styles.get(storyboard.style)
        ctx.report_progress(steps["media"]["start"], "Hình minh hoạ")
        media_provider = None
        if any(s.visual.type == "stock" for s in storyboard.scenes):
            media_provider = self._resolve_optional("media", gen, project_settings)
        scene_media: list[SceneMedia] = []
        for scene in storyboard.scenes:
            asset = (row.assets or {}).get(scene.id) or synth["assets"].get(scene.id)
            image_path = None
            if scene.visual.type == "image" and scene.visual.image:
                candidate = self.settings.data_dir / scene.visual.image
                image_path = candidate if candidate.is_file() else None
            elif scene.visual.type == "stock":
                image_path = await self._stock_image(
                    media_provider, scene.visual.query or scene.visual.title or storyboard.title,
                    aspect, ctx)
            scene_media.append(SceneMedia(
                scene=scene,
                audio_path=(self.settings.data_dir / asset["audio"]) if asset else None,
                audio_duration_s=float(asset["duration_s"]) if asset else 0.0,
                words=[WordTiming(**w) for w in asset["words"]] if asset else [],
                image_path=image_path,
            ))
        branding_cfg = self.branding()
        branding = Branding()
        if branding_cfg.get("enabled"):
            branding = Branding(
                logo_path=self._asset_path(branding_cfg.get("logo_asset_id")),
                logo_position=branding_cfg["logo_position"],
                logo_scale=float(branding_cfg["logo_scale"]),
                logo_opacity=float(branding_cfg["logo_opacity"]),
                intro_text=branding_cfg.get("intro_text") or "",
                outro_text=branding_cfg.get("outro_text") or "",
                card_seconds=float(branding_cfg.get("card_seconds") or 2.5),
            )
        project_dir = self.settings.project_dir(project_id)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = "" if aspect == storyboard.aspect else "_" + aspect.replace(":", "x")
        basename = safe_filename(f"{storyboard.title[:60]}_{style.id}{suffix}_{stamp}", "video")
        plan = RenderPlan(
            title=storyboard.title, style_id=style.id, style_data=style.data,
            style_dirs=style.dirs, aspect=aspect, language=storyboard.language,
            scenes=scene_media, work_dir=project_dir / "work",
            out_dir=project_dir / "outputs", basename=basename, branding=branding,
            music_path=self._asset_path(gen.music_asset_id), music_volume=gen.music_volume,
            burn_subtitles=gen.burn_subtitles and gen.captions, preview=preview,
        )
        result = await self.renderer.render(plan, _sub_ctx(ctx, steps["render"]["start"], 100))
        data_dir = self.settings.data_dir
        outputs = {"video": result.video_path.relative_to(data_dir).as_posix()}
        for ext, path in result.subtitles.items():
            outputs[ext] = path.relative_to(data_dir).as_posix()
        if result.thumbnail_path:
            outputs["thumbnail"] = result.thumbnail_path.relative_to(data_dir).as_posix()
        size = result.video_path.stat().st_size
        snapshot = {"title": storyboard.title, "scenes": [
            {"id": sc.id, "narration": sc.narration, "duration": sc.duration,
             "visual": sc.visual.model_dump(mode="json", exclude_none=True),
             "overlays": [o.model_dump(mode="json") for o in sc.overlays]}
            for sc in storyboard.scenes]}
        self._finish_render(render_id, True, status="done", outputs=outputs,
                            duration_s=round(result.duration_s, 3), size_bytes=size,
                            finished_at=utcnow(),
                            settings={"preview": preview,
                                      "variant": aspect != storyboard.aspect,
                                      "encoder": result.encoder, "width": result.width,
                                      "height": result.height,
                                      "burned_subtitles": plan.burn_subtitles,
                                      "content_hash": content, "timeline": result.timeline,
                                      "transition": result.transition,
                                      "snapshot": snapshot})
        ctx.log(f"Xuất xong {result.video_path.name} ({result.duration_s:.1f}s, "
                f"{size / 1e6:.1f} MB, {result.width}x{result.height}, {result.encoder})")
        return {"render_id": render_id, "outputs": outputs, "duration_s": result.duration_s,
                "encoder": result.encoder, "tts_cache_hits": synth["cache_hits"]}
