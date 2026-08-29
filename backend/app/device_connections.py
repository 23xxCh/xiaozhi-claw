import asyncio
import contextlib
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(frozen=True, slots=True)
class ConnectionLease:
    serial_number: str
    websocket: WebSocket
    session_id: str
    generation: int
    send_lock: asyncio.Lock


class DeviceConnectionManager:
    """Tracks the single active pilot WebSocket for each device serial number."""

    def __init__(self) -> None:
        self._connections: dict[str, ConnectionLease] = {}
        self._generations: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self, serial_number: str, websocket: WebSocket, session_id: str
    ) -> ConnectionLease:
        async with self._lock:
            previous = self._connections.get(serial_number)
            generation = self._generations.get(serial_number, 0) + 1
            self._generations[serial_number] = generation
            lease = ConnectionLease(
                serial_number=serial_number,
                websocket=websocket,
                session_id=session_id,
                generation=generation,
                send_lock=asyncio.Lock(),
            )
            self._connections[serial_number] = lease
        if previous is not None and previous.websocket is not websocket:
            with contextlib.suppress(RuntimeError):
                await previous.websocket.close(code=1012, reason="device reconnected")
        return lease

    async def disconnect(self, lease: ConnectionLease) -> None:
        async with self._lock:
            current = self._connections.get(lease.serial_number)
            if current is lease:
                self._connections.pop(lease.serial_number, None)

    async def is_current(self, lease: ConnectionLease) -> bool:
        async with self._lock:
            return self._connections.get(lease.serial_number) is lease

    async def send_json_for_lease(
        self, lease: ConnectionLease, payload: dict[str, object]
    ) -> bool:
        if not await self.is_current(lease):
            return False
        message = {"session_id": lease.session_id, **payload}
        try:
            async with lease.send_lock:
                if not await self.is_current(lease):
                    return False
                await lease.websocket.send_json(message)
        except RuntimeError:
            await self.disconnect(lease)
            return False
        return True

    async def send_bytes_for_lease(self, lease: ConnectionLease, payload: bytes) -> bool:
        if not await self.is_current(lease):
            return False
        try:
            async with lease.send_lock:
                if not await self.is_current(lease):
                    return False
                await lease.websocket.send_bytes(payload)
        except RuntimeError:
            await self.disconnect(lease)
            return False
        return True

    async def send_json(self, serial_number: str, payload: dict[str, object]) -> bool:
        async with self._lock:
            lease = self._connections.get(serial_number)
        if lease is None:
            return False
        return await self.send_json_for_lease(lease, payload)

    async def send_bytes(self, serial_number: str, payload: bytes) -> bool:
        async with self._lock:
            lease = self._connections.get(serial_number)
        if lease is None:
            return False
        return await self.send_bytes_for_lease(lease, payload)

    async def is_connected(self, serial_number: str) -> bool:
        async with self._lock:
            return serial_number in self._connections
