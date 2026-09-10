from types import SimpleNamespace

import pytest

from backend.realtime import s2s


@pytest.mark.parametrize("paced", [True, False])
async def test_upload_does_not_accumulate_send_and_timer_overhead(monkeypatch, paced):
    now = 0.0
    uploaded = []

    async def sleep(delay):
        nonlocal now
        now += delay + 0.010

    async def send(pcm, **kwargs):
        nonlocal now
        now += 0.005
        uploaded.append(pcm)

    async def chunks():
        for index in range(100):
            yield bytes([index]) * 640

    monkeypatch.setattr(s2s, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(s2s.asyncio, "sleep", sleep)
    source = object.__new__(s2s.SpeechToSpeechInput)
    source.backend = SimpleNamespace(send_audio=send)
    source.decoder = SimpleNamespace(chunks=chunks)
    source.generation = 1
    source._pcm_bytes = 0
    source._started_at = 0.0
    source.turn_id = "pacing-test"
    source._send_seconds = 0.0
    source._pacing_seconds = 0.0
    source._pace_input = paced
    await source._upload()
    if paced:
        assert 1.98 <= now <= 2.02
    else:
        assert now == pytest.approx(0.5)
        assert source._pacing_seconds == 0
    assert uploaded == [bytes([index]) * 640 for index in range(100)]
    assert source._pcm_bytes == 64000
    assert source._send_seconds == pytest.approx(0.5)
    assert source._send_seconds + source._pacing_seconds == pytest.approx(now)
