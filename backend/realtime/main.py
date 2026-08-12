from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.catalog import ensure_catalog
from backend.app.config import get_settings
from backend.app.db import Base, create_engine, create_session_factory
from backend.app.device_connections import DeviceConnectionManager
from backend.app.providers import create_fallback_providers
from backend.app.routers import device_ws, health

from .commands import DeviceCommandDispatcher
from .providers import create_realtime_providers

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    if settings.app_env in {"development", "test"}:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    async with app.state.session_factory() as session:
        await ensure_catalog(session)
        await session.commit()
    dispatcher = DeviceCommandDispatcher(
        app.state.session_factory,
        app.state.device_connections,
        settings.command_poll_interval_seconds,
    )
    dispatcher.start()
    try:
        yield
    finally:
        await dispatcher.stop()
        await engine.dispose()


app = FastAPI(
    title="Hensun Realtime Gateway",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.settings = settings
app.state.realtime_providers = create_realtime_providers(settings)
app.state.fallback_providers = create_fallback_providers(settings)
app.state.device_connections = DeviceConnectionManager()
app.include_router(health.router)
app.include_router(device_ws.router)
