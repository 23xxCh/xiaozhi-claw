from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.catalog import ensure_catalog
from backend.app.config import get_settings
from backend.app.db import Base, create_engine, create_session_factory
from backend.app.device_connections import DeviceConnectionManager
from backend.app.pricing import backfill_unpriced_provider_usage
from backend.app.provider_network import install_provider_host_overrides
from backend.app.providers import create_fallback_providers
from backend.app.routers import device_ws, health
from backend.app.runtime_state import RuntimeStateReaper, reconcile_stale_runtime_state

from .commands import DeviceCommandDispatcher
from .providers import create_realtime_providers

settings = get_settings()
install_provider_host_overrides(settings.provider_host_overrides)


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
        await backfill_unpriced_provider_usage(session)
        await session.commit()
    await reconcile_stale_runtime_state(
        app.state.session_factory,
        offline_after_seconds=settings.device_offline_after_seconds,
    )
    dispatcher = DeviceCommandDispatcher(
        app.state.session_factory,
        app.state.device_connections,
        settings.command_poll_interval_seconds,
    )
    dispatcher.start()
    reaper = RuntimeStateReaper(
        app.state.session_factory,
        offline_after_seconds=settings.device_offline_after_seconds,
    )
    reaper.start()
    try:
        yield
    finally:
        await dispatcher.stop()
        await reaper.stop()
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
