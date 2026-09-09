from types import SimpleNamespace
import pytest
from backend.realtime import s2s


async def test_upload_does_not_accumulate_send_and_timer_overhead(monkeypatch):
    now = 0.
    async def sleep(delay):
        nonlocal now
        now += delay + .010
    async def send(pcm, **kwargs):
        nonlocal now
        now += .005
    async def chunks():
        for _ in range(100):
            yield b'\0'*640
    monkeypatch.setattr(s2s, 'time', SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(s2s.asyncio, 'sleep', sleep)
    source = object.__new__(s2s.SpeechToSpeechInput)
    source.backend = SimpleNamespace(send_audio=send)
    source.decoder = SimpleNamespace(chunks=chunks)
    source.generation = 1
    source._pcm_bytes = 0
    source._started_at = 0.
    source.turn_id = 'pacing-test'
    source._send_seconds = 0.
    source._pacing_seconds = 0.
    await source._upload()
    assert 1.98 <= now <= 2.02
    assert source._pcm_bytes == 64000
    assert source._send_seconds == pytest.approx(.5)
    assert source._send_seconds + source._pacing_seconds == pytest.approx(now)
