import time

import pytest

from backend.realtime.session import PlaybackHandshake


@pytest.mark.asyncio
async def test_missing_drained_ack_uses_short_compatibility_grace_period() -> None:
    playback = PlaybackHandshake(drain_timeout_seconds=0.01)
    reply_id = playback.begin("turn-1")

    started_at = time.perf_counter()
    assert await playback.wait_drained(reply_id) is False

    assert time.perf_counter() - started_at < 0.2


def test_acknowledgement_requires_matching_turn_and_reply() -> None:
    playback = PlaybackHandshake()
    reply_id = playback.begin("turn-current")

    assert playback.acknowledge("ready", reply_id, "turn-stale") is False
    assert playback.ready.is_set() is False
    assert playback.acknowledge("ready", reply_id, "turn-current") is True
    assert playback.ready.is_set() is True
