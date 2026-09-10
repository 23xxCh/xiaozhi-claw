import struct
from types import SimpleNamespace

import pytest

from backend.realtime import s2s


@pytest.mark.parametrize("paced", [True, False])
async def test_upload_does_not_accumulate_send_and_timer_overhead(monkeypatch, paced, caplog):
    caplog.set_level("INFO", logger="uvicorn.error")
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

    assert "decoded_samples=32000 avg_abs=12722 peak=25443" in caplog.text


async def test_input_envelope_preserves_partial_tail_and_bounds_log(caplog):
    caplog.set_level("INFO", logger="uvicorn.error")
    pcm = b"".join(struct.pack("<h", value) * 3200 for value in range(11))
    pcm += struct.pack("<h", -32768) * 160
    uploaded = []

    async def chunks():
        for offset in range(0, len(pcm), 514):
            yield pcm[offset:offset + 514]

    async def send(chunk, **kwargs):
        uploaded.append(chunk)

    source = object.__new__(s2s.SpeechToSpeechInput)
    source.backend = SimpleNamespace(send_audio=send)
    source.decoder = SimpleNamespace(chunks=chunks)
    source.generation = 1
    source._pcm_bytes = 0
    source._started_at = s2s.time.monotonic()
    source.turn_id = "envelope-test"
    source._send_seconds = source._pacing_seconds = 0.0
    source._pace_input = False
    await source._upload()

    assert b"".join(uploaded) == pcm
    envelope = next(record.message for record in caplog.records if "pcm_envelope" in record.message)
    assert "first=[(0, 0, 0), (200, 1, 1), (400, 2, 2), (600, 3, 3), (800, 4, 4)]" in envelope
    assert (
        "last=[(1400, 7, 7), (1600, 8, 8), (1800, 9, 9), "
        "(2000, 10, 10), (2200, 32768, 32768)]"
    ) in envelope
