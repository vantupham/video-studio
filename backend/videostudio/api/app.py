from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from videostudio import __version__
from videostudio.config import Settings
from videostudio.db import make_engine, make_session_factory, run_migrations
from videostudio.jobs import EventBus, JobQueue
from videostudio.jobs.handlers import register_builtin_handlers
from videostudio.pipeline.service import PipelineService
from videostudio.plugins import build_registry
from videostudio.providers.service import ProviderService
from videostudio.secretstore import create_secret_store

from . import media, routes_jobs, routes_pipeline, routes_projects, routes_providers, routes_system
from .auth import TOKEN_HEADER, require_token
from .services import Services

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, start_workers: bool = True) -> FastAPI:
    """Application factory. ``settings`` defaults to environment configuration."""

    if settings is None:
        settings = Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        engine = make_engine(settings.db_path)
        run_migrations(engine)
        session_factory = make_session_factory(engine)
        registry = build_registry(settings.plugins_dir)
        bus = EventBus()
        secrets = create_secret_store(settings.secrets_dir)
        providers = ProviderService(session_factory, registry.providers, secrets,
                                    models_dir=settings.data_dir / "models",
                                    cache_dir=settings.data_dir / "cache")
        providers.seed_defaults()
        pipeline = PipelineService(settings, session_factory, providers)
        queue = JobQueue(session_factory, bus, workers=settings.workers)
        register_builtin_handlers(queue, session_factory, settings, registry, pipeline)
        queue.requeue_stale()
        app.state.services = Services(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            registry=registry,
            bus=bus,
            queue=queue,
            secrets=secrets,
            providers=providers,
            pipeline=pipeline,
        )
        if start_workers:
            queue.start()
        log.info("Backend ready (data dir: %s)", settings.data_dir)
        try:
            yield
        finally:
            queue.stop()
            engine.dispose()

    app = FastAPI(
        title="Video Studio backend",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.dev else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?" if settings.dev else None,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", TOKEN_HEADER],
    )
    protected = [Depends(require_token)]
    app.include_router(routes_system.router, dependencies=protected)
    app.include_router(routes_projects.router, dependencies=protected)
    app.include_router(routes_jobs.router, dependencies=protected)
    app.include_router(routes_providers.router, dependencies=protected)
    app.include_router(routes_pipeline.router, dependencies=protected)
    app.include_router(media.router)
    app.include_router(routes_jobs.ws_router)
    return app
