"""Built-in job types: ``demo`` (proves the queue) and ``ingest`` (runs input adapters)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from videostudio.config import Settings
from videostudio.db import IRDocument, Project, Source, new_id
from videostudio.plugins import JobContext, PluginRegistry, SourceRef

from .queue import JobQueue, JobSnapshot


async def demo_job(job: JobSnapshot, ctx: JobContext) -> dict[str, Any]:
    seconds = float(job.payload.get("seconds", 5))
    steps = max(1, int(job.payload.get("steps", 10)))
    fail_at = job.payload.get("fail_at_step")
    ctx.log(f"Demo job: {steps} steps over {seconds}s")
    for step in range(1, steps + 1):
        ctx.check_cancelled()
        await asyncio.sleep(seconds / steps)
        if fail_at is not None and step == int(fail_at):
            raise RuntimeError(f"Demo failure at step {step}")
        ctx.report_progress(step * 100 / steps, f"Bước {step}/{steps}")
        ctx.log(f"Step {step}/{steps} done")
    return {"steps": steps, "seconds": seconds}


def source_ref_for(source: Source, settings: Settings) -> SourceRef:
    path = settings.data_dir / Path(source.rel_path)
    if source.kind == "file":
        return SourceRef(type="file", uri=source.filename, path=path, filename=source.filename)
    return SourceRef(type="text", uri=f"inline:{source.name}", path=path,
                     filename=source.filename)


def make_ingest_job(
    session_factory: sessionmaker[Session], settings: Settings, registry: PluginRegistry
):
    async def ingest_job(job: JobSnapshot, ctx: JobContext) -> dict[str, Any]:
        if not job.project_id:
            raise ValueError("ingest job requires project_id")
        wanted: list[str] | None = job.payload.get("source_ids")
        with session_factory() as s:
            project = s.get(Project, job.project_id)
            if project is None:
                raise ValueError(f"Project {job.project_id} not found")
            stmt = select(Source).where(Source.project_id == project.id).order_by(
                Source.created_at
            )
            sources = [src for src in s.scalars(stmt) if wanted is None or src.id in wanted]
        if not sources:
            raise ValueError("Dự án chưa có nguồn nội dung nào để xử lý")

        ir_dir = settings.project_dir(job.project_id) / "ir"
        ir_dir.mkdir(parents=True, exist_ok=True)
        documents = []
        n = len(sources)
        for idx, source in enumerate(sources):
            ctx.check_cancelled()
            adapter = registry.get_input_adapter(source.adapter_id)
            ctx.log(f"[{idx + 1}/{n}] {source.name} → adapter '{adapter.id}'")
            lo, hi = idx * 100 / n, (idx + 1) * 100 / n

            def sub_progress(pct: int, msg: str | None, lo=lo, hi=hi, name=source.name) -> None:
                ctx.report_progress(lo + (hi - lo) * pct / 100,
                                    f"{name}: {msg}" if msg else name)

            sub_ctx = replace(ctx, config=dict(source.config or {}), progress_cb=sub_progress)
            ir = await adapter.ingest(source_ref_for(source, settings), sub_ctx)
            ctx.check_cancelled()

            doc_id = new_id()
            out_path = ir_dir / f"{doc_id}.json"
            payload = ir.model_dump(mode="json")
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            stats = ir.stats()
            with session_factory.begin() as s:
                s.add(IRDocument(
                    id=doc_id,
                    project_id=job.project_id,
                    source_id=source.id,
                    job_id=job.id,
                    adapter_id=adapter.id,
                    title=ir.title,
                    rel_path=out_path.relative_to(settings.data_dir).as_posix(),
                    data=payload,
                    stats=stats,
                ))
            ctx.log(f"Saved IR {out_path.name}: {stats}")
            documents.append({"id": doc_id, "source_id": source.id, "title": ir.title,
                              "stats": stats})
            ctx.report_progress(hi, f"Xong {idx + 1}/{n}")
        return {"documents": documents}

    return ingest_job


def register_builtin_handlers(
    queue: JobQueue,
    session_factory: sessionmaker[Session],
    settings: Settings,
    registry: PluginRegistry,
) -> None:
    queue.register("demo", demo_job)
    queue.register("ingest", make_ingest_job(session_factory, settings, registry))
    queue.set_project_dir_resolver(settings.project_dir)
