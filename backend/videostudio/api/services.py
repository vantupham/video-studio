from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, WebSocket
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from videostudio.config import Settings
from videostudio.jobs import EventBus, JobQueue
from videostudio.pipeline.service import PipelineService
from videostudio.plugins import PluginRegistry
from videostudio.providers.service import ProviderService
from videostudio.secretstore import SecretStore


@dataclass
class Services:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    registry: PluginRegistry
    bus: EventBus
    queue: JobQueue
    secrets: SecretStore
    providers: ProviderService
    pipeline: PipelineService


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_ws_services(websocket: WebSocket) -> Services:
    return websocket.app.state.services
