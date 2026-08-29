import asyncio
import time
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import WebSocket

from backend.app.device_connections import ConnectionLease
from backend.realtime.playback import PlaybackCoordinator


class _PlaybackTransport:
    def __init__(self, playback: PlaybackCoordinator, *, delivered: bool = True) -> None:
        self.playback = playback
        self.delivered = delivered
        self.messages: list[dict[str, object]] = []

    async def send_json_for_lease(
        self, lease: ConnectionLease, payload: dict[str, object]
    ) -> bool:
        del lease
        self.messages.append(payload)
        if not self.delivered:
            return False
        state = payload.get("state")
        reply_id = payload.get("reply_id")
        turn_id = payload.get("turn_id")
        if isinstance(state, str) and isinstance(reply_id, str) and isinstance(turn_id, str):
            self.playback.acknowledge(
                "ready" if state == "start" else "drained", reply_id, turn_id
            )
        return True


def _transport_fixture(
    playback: PlaybackCoordinator, *, delivered: bool = True
) -> tuple[WebSocket, ConnectionLease, _PlaybackTransport]:
    transport = _PlaybackTransport(playback, delivered=delivered)
    websocket = cast(
        WebSocket,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(device_connections=transport))),
    )
    lease = ConnectionLease(
        serial_number="device-1",
        websocket=websocket,
        session_id="session-1",
        generation=1,
        send_lock=asyncio.Lock(),
    )
    return websocket, lease, transport


@pytest.mark.asyncio
async def test_missing_drained_ack_uses_short_compatibility_grace_period() -> None:
    playback = PlaybackCoordinator(drain_timeout_seconds=0.01)
    reply_id = playback.begin("turn-1")

    started_at = time.perf_counter()
    assert await playback.wait_drained(reply_id) is False

    assert time.perf_counter() - started_at < 0.2


def test_acknowledgement_requires_matching_turn_and_reply() -> None:
    playback = PlaybackCoordinator()
    reply_id = playback.begin("turn-current")

    assert playback.acknowledge("ready", reply_id, "turn-stale") is False
    assert playback.ready.is_set() is False
    assert playback.acknowledge("ready", reply_id, "turn-current") is True
    assert playback.ready.is_set() is True


@pytest.mark.asyncio
async def test_start_and_stop_complete_one_owned_reply() -> None:
    playback = PlaybackCoordinator()
    playback.configure(strict_ack=True)
    websocket, lease, transport = _transport_fixture(playback)

    reply_id = await playback.start(websocket, lease, "turn-1")
    assert await playback.stop(
        websocket, lease, reply_id, "turn-1", wait_for_drain=True
    )

    assert [message["state"] for message in transport.messages] == ["start", "stop"]
    assert playback.last_drain_acknowledged is True
    assert playback.reply_id is None


@pytest.mark.asyncio
async def test_expired_connection_lease_stops_before_audio() -> None:
    playback = PlaybackCoordinator()
    websocket, lease, _transport = _transport_fixture(playback, delivered=False)

    with pytest.raises(ConnectionError, match="lease expired"):
        await playback.start(websocket, lease, "turn-1")

    assert playback.reply_id is None
