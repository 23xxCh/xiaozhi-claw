import asyncio
import time
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import WebSocket

from backend.app.device_connections import ConnectionLease
from backend.realtime.playback import (
    PlaybackCoordinator,
    PlaybackReadyTimeout,
    PlaybackStopOutcome,
)


class _PlaybackTransport:
    def __init__(
        self,
        playback: PlaybackCoordinator,
        *,
        delivered: bool = True,
        acknowledge_start: bool = True,
    ) -> None:
        self.playback = playback
        self.delivered = delivered
        self.acknowledge_start = acknowledge_start
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
        if (
            isinstance(state, str)
            and isinstance(reply_id, str)
            and isinstance(turn_id, str)
            and (state != "start" or self.acknowledge_start)
        ):
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
    playback = PlaybackCoordinator(
        legacy_drain_timeout_seconds=0.01,
        strict_drain_timeout_seconds=0.05,
    )
    reply_id = playback.begin("turn-1")

    started_at = time.perf_counter()
    assert await playback.wait_drained(reply_id) is False

    assert time.perf_counter() - started_at < 0.2


@pytest.mark.asyncio
async def test_strict_ack_accepts_drain_after_legacy_grace_period() -> None:
    playback = PlaybackCoordinator(
        legacy_drain_timeout_seconds=0.01,
        strict_drain_timeout_seconds=0.05,
    )
    playback.configure(strict_ack=True)
    reply_id = playback.begin("turn-current")

    async def acknowledge_after_legacy_window() -> None:
        await asyncio.sleep(0.02)
        assert playback.acknowledge("drained", reply_id, "turn-current") is True

    acknowledge_task = asyncio.create_task(acknowledge_after_legacy_window())
    assert await playback.wait_drained(reply_id) is True
    await acknowledge_task


@pytest.mark.asyncio
async def test_strict_ready_accepts_ack_before_configured_three_second_deadline() -> None:
    playback = PlaybackCoordinator(ready_timeout_seconds=0.1)
    playback.configure(strict_ack=True)
    websocket, lease, transport = _transport_fixture(playback)
    transport.acknowledge_start = False

    async def acknowledge_near_deadline() -> None:
        await asyncio.sleep(0.04)
        assert playback.reply_id is not None
        assert playback.acknowledge("ready", playback.reply_id, "turn-current") is True

    acknowledge_task = asyncio.create_task(acknowledge_near_deadline())
    reply_id = await playback.start(websocket, lease, "turn-current")
    await acknowledge_task

    assert reply_id == playback.reply_id


@pytest.mark.asyncio
async def test_legacy_start_does_not_wait_for_ready() -> None:
    playback = PlaybackCoordinator(ready_timeout_seconds=60.0)
    websocket, lease, transport = _transport_fixture(playback)
    transport.acknowledge_start = False

    async def fail_if_waited(_: str) -> bool:
        raise AssertionError("legacy playback must not wait for ready")

    playback.wait_ready = fail_if_waited  # type: ignore[method-assign]

    reply_id = await playback.start(websocket, lease, "turn-legacy")

    assert reply_id == playback.reply_id
    assert [message["state"] for message in transport.messages] == ["start"]


@pytest.mark.asyncio
async def test_strict_ready_timeout_stops_and_clears_owned_reply() -> None:
    playback = PlaybackCoordinator(ready_timeout_seconds=0.001)
    playback.configure(strict_ack=True)
    websocket, lease, transport = _transport_fixture(playback)
    transport.acknowledge_start = False

    with pytest.raises(PlaybackReadyTimeout) as raised:
        await playback.start(websocket, lease, "turn-timeout")

    assert raised.value.turn_id == "turn-timeout"
    assert raised.value.reply_id
    assert [message["state"] for message in transport.messages] == ["start", "stop"]
    assert transport.messages[0]["reply_id"] == transport.messages[1]["reply_id"]
    assert playback.reply_id is None


def test_strict_ready_default_deadline_is_three_seconds() -> None:
    playback = PlaybackCoordinator()

    assert playback._ready_timeout_seconds == 3.0


def test_acknowledgement_requires_matching_turn_and_reply() -> None:
    playback = PlaybackCoordinator()
    reply_id = playback.begin("turn-current")

    assert playback.acknowledge("ready", reply_id, "turn-stale") is False
    assert playback.ready.is_set() is False
    assert playback.acknowledge("ready", reply_id, "turn-current") is True
    assert playback.ready.is_set() is True


def test_late_acknowledgement_is_rejected_after_reply_is_cleared() -> None:
    playback = PlaybackCoordinator()
    reply_id = playback.begin("turn-retired")

    playback.clear(reply_id)

    assert playback.acknowledge("ready", reply_id, "turn-retired") is False
    assert playback.acknowledge("drained", reply_id, "turn-retired") is False


@pytest.mark.asyncio
async def test_start_and_stop_complete_one_owned_reply() -> None:
    playback = PlaybackCoordinator()
    playback.configure(strict_ack=True)
    websocket, lease, transport = _transport_fixture(playback)

    reply_id = await playback.start(websocket, lease, "turn-1")
    outcome = await playback.stop(
        websocket, lease, reply_id, "turn-1", wait_for_drain=True
    )

    assert [message["state"] for message in transport.messages] == ["start", "stop"]
    assert outcome == PlaybackStopOutcome(
        delivered=True,
        acknowledged=True,
        compatibility_accepted=False,
    )
    assert playback.last_drain_acknowledged is True
    assert playback.reply_id is None


@pytest.mark.asyncio
async def test_legacy_stop_distinguishes_compatibility_from_acknowledgement() -> None:
    playback = PlaybackCoordinator(legacy_drain_timeout_seconds=0.001)
    websocket, lease, transport = _transport_fixture(playback)
    transport.acknowledge_start = False
    reply_id = await playback.start(websocket, lease, "turn-legacy")

    async def send_without_drain(
        lease: ConnectionLease, payload: dict[str, object]
    ) -> bool:
        del lease
        transport.messages.append(payload)
        return True

    transport.send_json_for_lease = send_without_drain  # type: ignore[method-assign]
    outcome = await playback.stop(
        websocket, lease, reply_id, "turn-legacy", wait_for_drain=True
    )

    assert outcome == PlaybackStopOutcome(
        delivered=True,
        acknowledged=False,
        compatibility_accepted=True,
    )


@pytest.mark.asyncio
async def test_expired_connection_lease_stops_before_audio() -> None:
    playback = PlaybackCoordinator()
    websocket, lease, _transport = _transport_fixture(playback, delivered=False)

    with pytest.raises(ConnectionError, match="lease expired"):
        await playback.start(websocket, lease, "turn-1")

    assert playback.reply_id is None
