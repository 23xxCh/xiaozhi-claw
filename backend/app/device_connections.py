import asyncio

from fastapi import WebSocket


class DeviceConnectionManager:
    """Tracks the single active pilot WebSocket for each device serial number."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

    async def connect(self, serial_number: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections[serial_number] = websocket

    async def disconnect(self, serial_number: str, websocket: WebSocket) -> None:
        async with self._lock:
            if self._connections.get(serial_number) is websocket:
                self._connections.pop(serial_number, None)

    async def send_json(self, serial_number: str, payload: dict[str, object]) -> bool:
        async with self._lock:
            websocket = self._connections.get(serial_number)
        if websocket is None:
            return False
        try:
            await websocket.send_json(payload)
        except RuntimeError:
            await self.disconnect(serial_number, websocket)
            return False
        return True

    async def is_connected(self, serial_number: str) -> bool:
        async with self._lock:
            return serial_number in self._connections
