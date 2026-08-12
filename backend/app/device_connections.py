import asyncio
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass
class DeviceConnection:
    websocket: WebSocket
    session_id: str
    send_lock: asyncio.Lock


class DeviceConnectionManager:
    """Tracks the single active pilot WebSocket for each device serial number."""

    def __init__(self) -> None:
        self._connections: dict[str, DeviceConnection] = {}
        self._lock = asyncio.Lock()

    async def connect(self, serial_number: str, websocket: WebSocket, session_id: str) -> None:
        async with self._lock:
            self._connections[serial_number] = DeviceConnection(
                websocket=websocket,
                session_id=session_id,
                send_lock=asyncio.Lock(),
            )

    async def disconnect(self, serial_number: str, websocket: WebSocket) -> None:
        async with self._lock:
            connection = self._connections.get(serial_number)
            if connection is not None and connection.websocket is websocket:
                self._connections.pop(serial_number, None)

    async def send_json(self, serial_number: str, payload: dict[str, object]) -> bool:
        async with self._lock:
            connection = self._connections.get(serial_number)
        if connection is None:
            return False
        message = {"session_id": connection.session_id, **payload}
        try:
            async with connection.send_lock:
                await connection.websocket.send_json(message)
        except RuntimeError:
            await self.disconnect(serial_number, connection.websocket)
            return False
        return True

    async def send_bytes(self, serial_number: str, payload: bytes) -> bool:
        async with self._lock:
            connection = self._connections.get(serial_number)
        if connection is None:
            return False
        try:
            async with connection.send_lock:
                await connection.websocket.send_bytes(payload)
        except RuntimeError:
            await self.disconnect(serial_number, connection.websocket)
            return False
        return True

    async def is_connected(self, serial_number: str) -> bool:
        async with self._lock:
            return serial_number in self._connections
