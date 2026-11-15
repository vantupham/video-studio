from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class SourceOut(BaseModel):
    id: str
    project_id: str
    name: str
    kind: str
    adapter_id: str
    config: dict[str, Any]
    filename: str
    size_bytes: int
    attachments: int = 0
    created_at: datetime


class DocumentSummary(BaseModel):
    id: str
    project_id: str
    source_id: str | None
    job_id: str | None
    adapter_id: str
    title: str
    rel_path: str
    stats: dict[str, Any]
    created_at: datetime


class DocumentOut(DocumentSummary):
    data: dict[str, Any]


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    source_count: int = 0
    document_count: int = 0


class ProjectDetail(ProjectOut):
    sources: list[SourceOut] = Field(default_factory=list)
    documents: list[DocumentSummary] = Field(default_factory=list)


class IngestRequest(BaseModel):
    source_ids: list[str] | None = None


class DemoJobRequest(BaseModel):
    seconds: float = Field(default=5, ge=0, le=600)
    steps: int = Field(default=10, ge=1, le=1000)
    fail_at_step: int | None = None


class SecretTestRequest(BaseModel):
    value: str = Field(min_length=1, max_length=100_000)
