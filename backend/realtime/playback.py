import asyncio
import logging
import uuid

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class PlaybackHandshake:
    def __init__(self) -> None:
        self.reply_id: str | None = None
        self.ready = asyncio.Event()
        self.drained = asyncio.Event()

    def begin(self) -> str:
        self.reply_id = str(uuid.uuid4())
        self.ready = asyncio.Event()
        self.drained = asyncio.Event()
        return self.reply_id

    def acknowledge(self, state: str, reply_id: str) -> bool:
        if not reply_id or reply_id != self.reply_id:
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
            await asyncio.wait_for(self.drained.wait(), timeout=5.0)
            return True
        except TimeoutError:
            return False

    def clear(self, reply_id: str | None = None) -> None:
        if reply_id is None or reply_id == self.reply_id:
            self.reply_id = None
            self.ready.set()
            self.drained.set()


async def start_playback(
    websocket: WebSocket,
    serial: str,
    playback: PlaybackHandshake,
) -> str:
    reply_id = playback.begin()
    await websocket.app.state.device_connections.send_json(
        serial, {"type": "tts", "state": "start", "reply_id": reply_id}
    )
    if not await playback.wait_ready(reply_id):
        logger.warning(
            "device %s did not acknowledge TTS ready; using paced compatibility mode",
            serial,
        )
    return reply_id


async def stop_playback(
    websocket: WebSocket,
    serial: str,
    playback: PlaybackHandshake,
    reply_id: str,
    *,
    wait_for_drain: bool,
) -> None:
    await websocket.app.state.device_connections.send_json(
        serial, {"type": "tts", "state": "stop", "reply_id": reply_id}
    )
    if wait_for_drain and not await playback.wait_drained(reply_id):
        logger.warning("device %s did not acknowledge TTS drained", serial)
    playback.clear(reply_id)
