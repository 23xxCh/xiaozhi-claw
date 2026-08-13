import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .audit import add_audit_event
from .models import ConversationSession, DeviceSession, DeviceSessionStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    sessions_closed: int = 0
    conversations_closed: int = 0


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def reconcile_stale_runtime_state(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    offline_after_seconds: int,
    now: datetime | None = None,
) -> ReconcileResult:
    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(seconds=offline_after_seconds)
    async with session_factory() as session:
        online_sessions = list(
            await session.scalars(
                select(DeviceSession).where(
                    DeviceSession.status == DeviceSessionStatus.ONLINE.value
                )
            )
        )
        fresh_by_device: dict[str, list[DeviceSession]] = {}
        sessions_closed = 0
        for device_session in online_sessions:
            if _as_utc(device_session.heartbeat_at) < cutoff:
                device_session.status = DeviceSessionStatus.OFFLINE.value
                device_session.disconnected_at = current_time
                sessions_closed += 1
            else:
                fresh_by_device.setdefault(device_session.device_id, []).append(device_session)

        open_conversations = list(
            await session.scalars(
                select(ConversationSession).where(
                    ConversationSession.ended_at.is_(None),
                    ConversationSession.started_at < cutoff,
                )
            )
        )
        conversations_closed = 0
        for conversation in open_conversations:
            started_at = _as_utc(conversation.started_at)
            has_matching_live_connection = any(
                abs((_as_utc(device_session.connected_at) - started_at).total_seconds()) <= 10
                for device_session in fresh_by_device.get(conversation.device_id, [])
            )
            if has_matching_live_connection:
                continue
            conversation.ended_at = current_time
            conversation.end_reason = "stale-recovered"
            conversations_closed += 1

        if sessions_closed or conversations_closed:
            add_audit_event(
                session,
                actor_type="system",
                actor_id="runtime-state-reaper",
                action="runtime.stale-state-recovered",
                payload={
                    "sessions_closed": sessions_closed,
                    "conversations_closed": conversations_closed,
                },
            )
            await session.commit()
        return ReconcileResult(
            sessions_closed=sessions_closed,
            conversations_closed=conversations_closed,
        )


class RuntimeStateReaper:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        offline_after_seconds: int,
    ) -> None:
        self.session_factory = session_factory
        self.offline_after_seconds = offline_after_seconds
        self.interval_seconds = max(10, min(30, offline_after_seconds))
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await reconcile_stale_runtime_state(
                    self.session_factory,
                    offline_after_seconds=self.offline_after_seconds,
                )
            except Exception:
                logger.exception("runtime state reconciliation failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
