from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from videostudio.db import IRDocument, Job, Project, Source, new_id, utcnow
from videostudio.jobs import QUEUED, RUNNING, InvalidJobState
from videostudio.plugins import SourceRef
from videostudio.textutil import decode_bytes, normalize_text, safe_filename

from .schemas import (
    DocumentOut,
    DocumentSummary,
    IngestRequest,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectUpdate,
    SourceOut,
)
from .services import Services, get_services
from .uploads import (
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_FILES,
    BundleError,
    extract_zip,
    pick_main,
    safe_rel_path,
    strip_common_root,
)

router = APIRouter(prefix="/projects", tags=["projects"])

MAX_SOURCE_BYTES = 50 * 1024 * 1024
BINARY_SOURCE_EXTS = {".docx", ".pdf"}


def _project_or_404(s, project_id: str) -> Project:
    project = s.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Không tìm thấy dự án")
    return project


def _source_out(src: Source) -> SourceOut:
    return SourceOut.model_validate(src, from_attributes=True)


def _doc_summary(doc: IRDocument) -> DocumentSummary:
    return DocumentSummary.model_validate(doc, from_attributes=True)


@router.get("", response_model=list[ProjectOut])
def list_projects(svc: Services = Depends(get_services)) -> list[ProjectOut]:
    with svc.session_factory() as s:
        src_counts = dict(
            s.execute(select(Source.project_id, func.count()).group_by(Source.project_id)).all()
        )
        doc_counts = dict(
            s.execute(
                select(IRDocument.project_id, func.count()).group_by(IRDocument.project_id)
            ).all()
        )
        projects = s.scalars(select(Project).order_by(Project.updated_at.desc())).all()
        return [
            ProjectOut(
                id=p.id, name=p.name, description=p.description, created_at=p.created_at,
                updated_at=p.updated_at, source_count=src_counts.get(p.id, 0),
                document_count=doc_counts.get(p.id, 0),
            )
            for p in projects
        ]


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, svc: Services = Depends(get_services)) -> ProjectDetail:
    with svc.session_factory.begin() as s:
        project = Project(name=body.name.strip(), description=body.description)
        s.add(project)
        s.flush()
        svc.settings.project_dir(project.id).mkdir(parents=True, exist_ok=True)
        return ProjectDetail(
            id=project.id, name=project.name, description=project.description,
            created_at=project.created_at, updated_at=project.updated_at,
        )


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str, svc: Services = Depends(get_services)) -> ProjectDetail:
    with svc.session_factory() as s:
        p = _project_or_404(s, project_id)
        return ProjectDetail(
            id=p.id, name=p.name, description=p.description, created_at=p.created_at,
            updated_at=p.updated_at, source_count=len(p.sources),
            document_count=len(p.documents),
            sources=[_source_out(x) for x in p.sources],
            documents=[_doc_summary(d) for d in reversed(p.documents)],
        )


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: str, body: ProjectUpdate, svc: Services = Depends(get_services)
) -> ProjectOut:
    with svc.session_factory.begin() as s:
        p = _project_or_404(s, project_id)
        if body.name is not None:
            p.name = body.name.strip()
        if body.description is not None:
            p.description = body.description
        s.flush()
        return ProjectOut(
            id=p.id, name=p.name, description=p.description, created_at=p.created_at,
            updated_at=p.updated_at, source_count=len(p.sources),
            document_count=len(p.documents),
        )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, svc: Services = Depends(get_services)) -> None:
    with svc.session_factory() as s:
        _project_or_404(s, project_id)
        active = s.scalars(
            select(Job.id).where(Job.project_id == project_id, Job.status.in_([QUEUED, RUNNING]))
        ).all()
    for job_id in active:
        try:
            svc.queue.cancel(job_id)
        except (KeyError, InvalidJobState):
            pass
    with svc.session_factory.begin() as s:
        s.delete(_project_or_404(s, project_id))
    shutil.rmtree(svc.settings.project_dir(project_id), ignore_errors=True)


# ------------------------------------------------------------------------------- sources


def _parse_config(raw: str | None, schema: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"config không phải JSON hợp lệ: {exc}") from exc
    if not isinstance(config, dict):
        raise HTTPException(422, "config phải là một object JSON")
    allowed = set(schema.get("properties", {}))
    unknown = set(config) - allowed
    if unknown and schema.get("additionalProperties") is False:
        raise HTTPException(422, f"Tuỳ chọn không hợp lệ: {', '.join(sorted(unknown))}")
    return config


@router.get("/{project_id}/sources", response_model=list[SourceOut])
def list_sources(project_id: str, svc: Services = Depends(get_services)) -> list[SourceOut]:
    with svc.session_factory() as s:
        return [_source_out(x) for x in _project_or_404(s, project_id).sources]


@router.post("/{project_id}/sources", response_model=SourceOut, status_code=201)
async def add_source(
    project_id: str,
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    paths: list[str] | None = Form(default=None),
    main: str | None = Form(default=None),
    text: str | None = Form(default=None),
    name: str | None = Form(default=None),
    adapter_id: str | None = Form(default=None),
    config: str | None = Form(default=None),
    svc: Services = Depends(get_services),
) -> SourceOut:
    """One document (``file``), pasted ``text``, or a bundle: a ``.zip`` in ``file`` or
    several ``files`` (with their relative ``paths`` when a folder was picked)."""
    with svc.session_factory() as s:
        _project_or_404(s, project_id)

    uploads = [f for f in (files or []) if f.filename]
    if file is not None and file.filename and file.filename.lower().endswith(".zip"):
        return await _add_bundle(svc, project_id, [file], None, main, name, adapter_id,
                                 config, is_zip=True)
    if uploads and (len(uploads) > 1 or paths):
        return await _add_bundle(svc, project_id, uploads, paths, main, name, adapter_id,
                                 config, is_zip=False)
    if uploads and file is None:
        file = uploads[0]

    if file is not None and file.filename:
        data = await file.read(MAX_SOURCE_BYTES + 1)
        kind = "file"
        filename = safe_filename(Path(file.filename).name, "source.txt")
        display = name or Path(file.filename).name
    elif text is not None and text.strip():
        data = normalize_text(text).encode("utf-8")
        kind = "text"
        display = (name or "").strip() or "Văn bản dán"
        filename = None  # decided once the adapter is known
    else:
        raise HTTPException(422, "Cần tải lên một file hoặc dán nội dung văn bản")
    if len(data) > MAX_SOURCE_BYTES:
        raise HTTPException(413, "Nội dung quá lớn (tối đa 50 MB)")

    adapter = _pick_adapter(svc, adapter_id, "file" if kind == "file" else "text", display,
                            filename)
    cfg = _parse_config(config, adapter.config_schema())

    if filename is None:
        ext = adapter.file_extensions[0] if adapter.file_extensions else ".txt"
        filename = safe_filename(display, "pasted") + ext

    source_id = new_id()
    folder = svc.settings.project_dir(project_id) / "sources" / source_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_bytes(data)
    return _save_source(svc, project_id, source_id, display, kind, adapter.id, cfg, filename,
                        path, len(data), 0)


def _pick_adapter(svc: Services, adapter_id: str | None, kind: str, display: str,
                  filename: str | None):
    if adapter_id:
        try:
            return svc.registry.get_input_adapter(adapter_id)
        except KeyError as exc:
            raise HTTPException(422, f"Không có adapter '{adapter_id}'") from exc
    adapter = svc.registry.find_input_adapter(
        SourceRef(type=kind, uri=display, filename=filename))  # type: ignore[arg-type]
    if adapter is None:
        raise HTTPException(422, "Không tìm thấy adapter phù hợp, hãy chọn adapter")
    return adapter


def _save_source(svc: Services, project_id: str, source_id: str, display: str, kind: str,
                 adapter_id: str, cfg: dict[str, Any], filename: str, path: Path, size: int,
                 attachments: int) -> SourceOut:
    with svc.session_factory.begin() as s:
        src = Source(
            id=source_id, project_id=project_id, name=display, kind=kind,
            adapter_id=adapter_id, config=cfg, filename=filename,
            rel_path=path.relative_to(svc.settings.data_dir).as_posix(), size_bytes=size,
            attachments=attachments,
        )
        s.add(src)
        _project_or_404(s, project_id).updated_at = utcnow()
        s.flush()
        return _source_out(src)


async def _add_bundle(svc: Services, project_id: str, uploads: list[UploadFile],
                      paths: list[str] | None, main: str | None, name: str | None,
                      adapter_id: str | None, config: str | None, *, is_zip: bool
                      ) -> SourceOut:
    source_id = new_id()
    folder = svc.settings.project_dir(project_id) / "sources" / source_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if is_zip:
            tmp = folder / ".upload.zip"
            size = await _stream_to(uploads[0], tmp, MAX_BUNDLE_BYTES)
            try:
                rels = extract_zip(tmp, folder)
            finally:
                tmp.unlink(missing_ok=True)
        else:
            if paths and len(paths) != len(uploads):
                raise BundleError("Số đường dẫn không khớp số tệp")
            wanted = [safe_rel_path((paths[i] if paths else None) or u.filename or "")
                      for i, u in enumerate(uploads)]
            pairs = [(u, r) for u, r in zip(uploads, wanted, strict=True) if r is not None]
            if len(pairs) > MAX_BUNDLE_FILES:
                raise BundleError(f"Quá nhiều tệp (tối đa {MAX_BUNDLE_FILES})")
            rels = strip_common_root([r for _, r in pairs])
            size = 0
            for (upload, _), rel in zip(pairs, rels, strict=True):
                size += await _stream_to(upload, folder / rel, MAX_BUNDLE_BYTES - size)
        main_rel = pick_main(rels, main)
    except BundleError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc
    except HTTPException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    display = name or (Path(uploads[0].filename or "").stem if is_zip else main_rel.name)
    adapter = _pick_adapter(svc, adapter_id, "file", main_rel.name, main_rel.name)
    cfg = _parse_config(config, adapter.config_schema())
    return _save_source(svc, project_id, source_id, display or main_rel.name, "file",
                        adapter.id, cfg, main_rel.name, folder / main_rel, size,
                        len(rels) - 1)


async def _stream_to(upload: UploadFile, dest: Path, budget: int) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with dest.open("wb") as out:
        while chunk := await upload.read(1 << 20):
            written += len(chunk)
            if written > budget:
                raise HTTPException(413, "Tổng dung lượng quá lớn (tối đa 200 MB)")
            out.write(chunk)
    return written


@router.get("/{project_id}/sources/{source_id}/content", response_class=PlainTextResponse)
def source_content(project_id: str, source_id: str, svc: Services = Depends(get_services)) -> str:
    with svc.session_factory() as s:
        src = s.get(Source, source_id)
        if src is None or src.project_id != project_id:
            raise HTTPException(404, "Không tìm thấy nguồn")
        path = svc.settings.data_dir / src.rel_path
    if path.suffix.lower() in BINARY_SOURCE_EXTS:
        raise HTTPException(415, "Không xem trước được tệp Word/PDF. Hãy bấm “Xử lý” rồi xem "
                                 "Content IR.")
    return normalize_text(decode_bytes(path.read_bytes()))


@router.delete("/{project_id}/sources/{source_id}", status_code=204)
def delete_source(project_id: str, source_id: str, svc: Services = Depends(get_services)) -> None:
    with svc.session_factory.begin() as s:
        src = s.get(Source, source_id)
        if src is None or src.project_id != project_id:
            raise HTTPException(404, "Không tìm thấy nguồn")
        folder = svc.settings.project_dir(project_id) / "sources" / src.id
        s.delete(src)
    shutil.rmtree(folder, ignore_errors=True)


# ------------------------------------------------------------------------------- ingest / IR


@router.post("/{project_id}/ingest", status_code=202)
def ingest(
    project_id: str, body: IngestRequest | None = None, svc: Services = Depends(get_services)
) -> dict[str, Any]:
    body = body or IngestRequest()
    with svc.session_factory() as s:
        project = _project_or_404(s, project_id)
        known = {x.id for x in project.sources}
    if not known:
        raise HTTPException(422, "Dự án chưa có nguồn nội dung nào")
    if body.source_ids is not None:
        missing = set(body.source_ids) - known
        if missing:
            raise HTTPException(422, f"Nguồn không thuộc dự án: {', '.join(sorted(missing))}")
    return svc.queue.enqueue("ingest", {"source_ids": body.source_ids}, project_id=project_id)


@router.get("/{project_id}/documents", response_model=list[DocumentSummary])
def list_documents(
    project_id: str, svc: Services = Depends(get_services)
) -> list[DocumentSummary]:
    with svc.session_factory() as s:
        _project_or_404(s, project_id)
        docs = s.scalars(
            select(IRDocument)
            .where(IRDocument.project_id == project_id)
            .order_by(IRDocument.created_at.desc())
        )
        return [_doc_summary(d) for d in docs]


@router.get("/{project_id}/documents/{doc_id}", response_model=DocumentOut)
def get_document(
    project_id: str, doc_id: str, svc: Services = Depends(get_services)
) -> DocumentOut:
    with svc.session_factory() as s:
        doc = s.get(IRDocument, doc_id)
        if doc is None or doc.project_id != project_id:
            raise HTTPException(404, "Không tìm thấy Content IR")
        return DocumentOut.model_validate(doc, from_attributes=True)
