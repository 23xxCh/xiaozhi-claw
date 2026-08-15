from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.realtime.commands import DeviceCommandDispatcher
from backend.realtime.providers import create_realtime_providers

from .catalog import ensure_catalog
from .config import Settings, get_settings
from .db import Base, create_engine, create_session_factory
from .device_connections import DeviceConnectionManager
from .errors import install_error_handlers
from .pricing import backfill_unpriced_provider_usage
from .providers import create_fallback_providers, create_providers
from .routers import (
    agent_memories,
    agents,
    auth,
    conversations,
    device_commands,
    device_configurations,
    device_events,
    device_ws,
    devices,
    health,
    memories,
    memory_portability,
    onboarding,
    ota,
    presets,
    profiles,
    staff,
    subscriptions,
    vision,
    xiaozhi_bootstrap,
)
from .runtime_state import RuntimeStateReaper, reconcile_stale_runtime_state
from .usage_profiles import ensure_all_adult_profiles


def create_app(settings: Settings | None = None, *, include_device_gateway: bool = True) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(resolved.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        if resolved.app_env in {"development", "test"}:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        async with app.state.session_factory() as session:
            await ensure_catalog(session)
            await ensure_all_adult_profiles(session)
            await backfill_unpriced_provider_usage(session)
            await session.commit()
        await reconcile_stale_runtime_state(
            app.state.session_factory,
            offline_after_seconds=resolved.device_offline_after_seconds,
        )
        dispatcher = None
        reaper = None
        if include_device_gateway:
            dispatcher = DeviceCommandDispatcher(
                app.state.session_factory,
                app.state.device_connections,
                resolved.command_poll_interval_seconds,
            )
            dispatcher.start()
            reaper = RuntimeStateReaper(
                app.state.session_factory,
                offline_after_seconds=resolved.device_offline_after_seconds,
            )
            reaper.start()
        try:
            yield
        finally:
            if dispatcher is not None:
                await dispatcher.stop()
            if reaper is not None:
                await reaper.stop()
            await engine.dispose()

    app = FastAPI(
        title="Hensun Desk Cloud",
        version="0.1.0",
        description="Commercial pilot control plane; not a production certification.",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.providers = create_providers(resolved)
    app.state.fallback_providers = create_fallback_providers(resolved)
    app.state.realtime_providers = create_realtime_providers(resolved)
    app.state.device_connections = DeviceConnectionManager()
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(agents.router)
    app.include_router(agent_memories.router)
    app.include_router(presets.router)
    app.include_router(profiles.router)
    app.include_router(onboarding.router)
    app.include_router(devices.router)
    app.include_router(device_configurations.router)
    app.include_router(device_configurations.admin_router)
    app.include_router(device_commands.router)
    app.include_router(memories.router)
    app.include_router(memory_portability.router)
    app.include_router(conversations.router)
    app.include_router(subscriptions.router)
    app.include_router(ota.router)
    app.include_router(staff.router)
    app.include_router(vision.router)
    if include_device_gateway:
        app.include_router(device_ws.router)
    app.include_router(device_events.router)
    app.include_router(xiaozhi_bootstrap.router)
    return app


app = create_app(include_device_gateway=False)
