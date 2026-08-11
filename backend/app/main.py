from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings, get_settings
from .db import Base, create_engine, create_session_factory
from .device_connections import DeviceConnectionManager
from .providers import create_providers
from .routers import (
    auth,
    device_events,
    device_ws,
    devices,
    health,
    memories,
    ota,
    subscriptions,
    xiaozhi_bootstrap,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(resolved.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        if resolved.app_env in {"development", "test"}:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Hensun Desk Cloud",
        version="0.1.0",
        description="Commercial pilot control plane; not a production certification.",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.providers = create_providers(resolved)
    app.state.device_connections = DeviceConnectionManager()
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(devices.router)
    app.include_router(memories.router)
    app.include_router(subscriptions.router)
    app.include_router(ota.router)
    app.include_router(device_ws.router)
    app.include_router(device_events.router)
    app.include_router(xiaozhi_bootstrap.router)
    return app


app = create_app()
