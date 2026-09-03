import asyncio
import logging
import uuid
from dataclasses import dataclass

from fastapi import WebSocket

from backend.app.device_connections import ConnectionLease

logger = logging.getLogger(__name__)
telemetry_logger = logging.getLogger("uvicorn.error")


class PlaybackReadyTimeout(RuntimeError):
    """A strict device did not acknowledge the owned reply before playback."""

    def __init__(self, *, reply_id: str, turn_id: str) -> None:
        self.reply_id = reply_id
        self.turn_id = turn_id
        super().__init__("tts-ready-timeout")


@dataclass(frozen=True)
class PlaybackStopOutcome:
    delivered: bool
    acknowledged: bool
    compatibility_accepted: bool
    waited_for_drain: bool = True

    def __bool__(self) -> bool:
        return self.delivered and (
            not self.waited_for_drain
            or self.acknowledged
            or self.compatibility_accepted
        )


class PlaybackCoordinator:
    """Own the ready/stop/drained lifecycle for exactly one device reply."""

    def __init__(
        self,
        *,
        ready_timeout_seconds: float = 3.0,
        legacy_drain_timeout_seconds: float = 1.0,
        strict_drain_timeout_seconds: float = 3.0,
    ) -> None:
        self.reply_id: str | None = None
        self.turn_id: str | None = None
        self.ready = asyncio.Event()
        self.drained = asyncio.Event()
        self._ready_timeout_seconds = ready_timeout_seconds
        self._legacy_drain_timeout_seconds = legacy_drain_timeout_seconds
        self._strict_drain_timeout_seconds = strict_drain_timeout_seconds
        self.strict_ack = False
        self.last_drain_acknowledged = False
        self._start_sent_at: float | None = None

    def configure(self, *, strict_ack: bool) -> None:
        self.strict_ack = strict_ack

    def begin(self, turn_id: str) -> str:
        self.reply_id = str(uuid.uuid4())
        self.turn_id = turn_id
        self.ready = asyncio.Event()
        self.drained = asyncio.Event()
        self.last_drain_acknowledged = False
        self._start_sent_at = None
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
            await asyncio.wait_for(
                self.ready.wait(), timeout=self._ready_timeout_seconds
            )
            return True
        except TimeoutError:
            return False

    async def wait_drained(self, reply_id: str) -> bool:
        if reply_id != self.reply_id:
            return False
        timeout_seconds = (
            self._strict_drain_timeout_seconds
            if self.strict_ack
            else self._legacy_drain_timeout_seconds
        )
        try:
            await asyncio.wait_for(self.drained.wait(), timeout=timeout_seconds)
            return True
        except TimeoutError:
            return False

    def clear(self, reply_id: str | None = None) -> None:
        if reply_id is None or reply_id == self.reply_id:
            self.reply_id = None
            self.turn_id = None
            self._start_sent_at = None
            self.ready.set()
            self.drained.set()

    async def initiate(
        self, websocket: WebSocket, lease: ConnectionLease, turn_id: str
    ) -> str:
        reply_id = self.begin(turn_id)
        started_at = asyncio.get_running_loop().time()
        delivered = await websocket.app.state.device_connections.send_json_for_lease(
            lease,
            {"type": "tts", "state": "start", "turn_id": turn_id, "reply_id": reply_id},
        )
        if not delivered:
            self.clear(reply_id)
            raise ConnectionError("device connection lease expired before TTS start")
        self._start_sent_at = started_at
        telemetry_logger.info(
            "tts.start sent serial=%s lease_generation=%d turn_id=%s reply_id=%s",
            lease.serial_number,
            lease.generation,
            turn_id,
            reply_id,
        )
        return reply_id

    async def ensure_ready(
        self,
        websocket: WebSocket,
        lease: ConnectionLease,
        reply_id: str,
        turn_id: str,
    ) -> None:
        started_at = self._start_sent_at or asyncio.get_running_loop().time()
        if not self.strict_ack:
            telemetry_logger.info(
                "legacy device %s using paced playback compatibility mode",
                lease.serial_number,
            )
            return
        if not await self.wait_ready(reply_id):
            logger.warning(
                "tts.ready timed out serial=%s lease_generation=%d turn_id=%s "
                "reply_id=%s wait_ms=%d",
                lease.serial_number,
                lease.generation,
                turn_id,
                reply_id,
                int((asyncio.get_running_loop().time() - started_at) * 1000),
            )
            await self.stop(
                websocket,
                lease,
                reply_id,
                turn_id,
                wait_for_drain=False,
            )
            raise PlaybackReadyTimeout(reply_id=reply_id, turn_id=turn_id)
        else:
            telemetry_logger.info(
                "tts.ready received serial=%s lease_generation=%d turn_id=%s "
                "reply_id=%s wait_ms=%d",
                lease.serial_number,
                lease.generation,
                turn_id,
                reply_id,
                int((asyncio.get_running_loop().time() - started_at) * 1000),
            )

    async def start(self, websocket: WebSocket, lease: ConnectionLease, turn_id: str) -> str:
        reply_id = await self.initiate(websocket, lease, turn_id)
        await self.ensure_ready(websocket, lease, reply_id, turn_id)
        return reply_id

    async def stop(
        self,
        websocket: WebSocket,
        lease: ConnectionLease,
        reply_id: str,
        turn_id: str,
        *,
        wait_for_drain: bool,
    ) -> PlaybackStopOutcome:
        delivered = await websocket.app.state.device_connections.send_json_for_lease(
            lease,
            {"type": "tts", "state": "stop", "turn_id": turn_id, "reply_id": reply_id},
        )
        if not delivered:
            self.clear(reply_id)
            return PlaybackStopOutcome(
                delivered=False,
                acknowledged=False,
                compatibility_accepted=False,
                waited_for_drain=wait_for_drain,
            )
        acknowledged = wait_for_drain and await self.wait_drained(reply_id)
        self.last_drain_acknowledged = acknowledged
        compatibility_accepted = wait_for_drain and not acknowledged and not self.strict_ack
        if wait_for_drain and not acknowledged:
            logger.warning("device %s did not acknowledge TTS drained", lease.serial_number)
        self.clear(reply_id)
        return PlaybackStopOutcome(
            delivered=True,
            acknowledged=acknowledged,
            compatibility_accepted=compatibility_accepted,
            waited_for_drain=wait_for_drain,
        )
