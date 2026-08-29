import asyncio
import logging
import uuid

from fastapi import WebSocket

from backend.app.device_connections import ConnectionLease

logger = logging.getLogger(__name__)


class PlaybackCoordinator:
    """Own the ready/stop/drained lifecycle for exactly one device reply."""

    def __init__(self, *, drain_timeout_seconds: float = 1.0) -> None:
        self.reply_id: str | None = None
        self.turn_id: str | None = None
        self.ready = asyncio.Event()
        self.drained = asyncio.Event()
        self._drain_timeout_seconds = drain_timeout_seconds
        self.strict_ack = False
        self.last_drain_acknowledged = False

    def configure(self, *, strict_ack: bool) -> None:
        self.strict_ack = strict_ack

    def begin(self, turn_id: str) -> str:
        self.reply_id = str(uuid.uuid4())
        self.turn_id = turn_id
        self.ready = asyncio.Event()
        self.drained = asyncio.Event()
        self.last_drain_acknowledged = False
        return self.reply_id

    def acknowledge(self, state: str, reply_id: str, turn_id: str = "") -> bool:
        if not reply_id or reply_id != self.reply_id:
            return False
        if turn_id and self.turn_id and turn_id != self.turn_id:
            return False
        if state == "ready":
            self.ready.set()
            return True
        if state == "drained":
            self.drained.set()
            return True
        return False

    async def wait_ready(self, reply_id: str) -> bool:
        if reply_id != self.reply_id:
            return False
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=2.0)
            return True
        except TimeoutError:
            return False

    async def wait_drained(self, reply_id: str) -> bool:
        if reply_id != self.reply_id:
            return False
        try:
            await asyncio.wait_for(self.drained.wait(), timeout=self._drain_timeout_seconds)
            return True
        except TimeoutError:
            return False

    def clear(self, reply_id: str | None = None) -> None:
        if reply_id is None or reply_id == self.reply_id:
            self.reply_id = None
            self.turn_id = None
            self.ready.set()
            self.drained.set()

    async def start(self, websocket: WebSocket, lease: ConnectionLease, turn_id: str) -> str:
        reply_id = self.begin(turn_id)
        delivered = await websocket.app.state.device_connections.send_json_for_lease(
            lease,
            {"type": "tts", "state": "start", "turn_id": turn_id, "reply_id": reply_id},
        )
        if not delivered:
            self.clear(reply_id)
            raise ConnectionError("device connection lease expired before TTS start")
        if not await self.wait_ready(reply_id):
            if self.strict_ack:
                self.clear(reply_id)
                raise TimeoutError("tts-ready-timeout")
            logger.warning(
                "legacy device %s did not acknowledge TTS ready; using paced compatibility mode",
                lease.serial_number,
            )
        return reply_id

    async def stop(
        self,
        websocket: WebSocket,
        lease: ConnectionLease,
        reply_id: str,
        turn_id: str,
        *,
        wait_for_drain: bool,
    ) -> bool:
        delivered = await websocket.app.state.device_connections.send_json_for_lease(
            lease,
            {"type": "tts", "state": "stop", "turn_id": turn_id, "reply_id": reply_id},
        )
        if not delivered:
            self.clear(reply_id)
            return False
        drained = not wait_for_drain or await self.wait_drained(reply_id)
        self.last_drain_acknowledged = drained
        if not drained:
            logger.warning("device %s did not acknowledge TTS drained", lease.serial_number)
        self.clear(reply_id)
        return drained or not self.strict_ack
