import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.device_connections import DeviceConnectionManager
from backend.app.models import Device, DeviceCommand, DeviceCommandStatus

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class DeviceCommandDispatcher:
    """One database Outbox poller per gateway process, not per device connection."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        connections: DeviceConnectionManager,
        interval_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.connections = connections
        self.interval_seconds = interval_seconds
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
                await self._dispatch_batch()
            except Exception:
                logger.exception("device command dispatch failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)

    async def _dispatch_batch(self) -> None:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(DeviceCommand, Device.serial_number)
                    .join(Device, Device.id == DeviceCommand.device_id)
                    .where(DeviceCommand.status == DeviceCommandStatus.PENDING.value)
                    .order_by(DeviceCommand.created_at)
                    .limit(100)
                )
            ).all()
            now = datetime.now(UTC)
            for command, serial in rows:
                if command.expires_at and _as_utc(command.expires_at) <= now:
                    command.status = DeviceCommandStatus.EXPIRED.value
                    continue
                try:
                    payload = json.loads(command.payload_json)
                except json.JSONDecodeError:
                    command.status = DeviceCommandStatus.FAILED.value
                    command.error_code = "invalid-payload"
                    continue
                if command.command_type == "ownership-revoked":
                    epoch = payload.get("reset_epoch") if isinstance(payload, dict) else None
                    if type(epoch) is not int or epoch < 1:
                        command.status = DeviceCommandStatus.FAILED.value
                        command.error_code = "invalid-reset-epoch"
                        continue
                    if await self.connections.revoke_ownership(serial, epoch):
                        command.status = DeviceCommandStatus.DELIVERED.value
                        command.delivered_at = now
                    # Other gateway processes still need to observe this command
                    # when the device is not connected to this process.
                    continue
                if await self.connections.send_json(serial, payload):
                    command.status = DeviceCommandStatus.DELIVERED.value
                    command.delivered_at = now
            await session.commit()
