import asyncio
import math
import shutil
import struct

import pytest

from backend.app.audio_formats import opus_packets_to_ogg
from backend.realtime.media import OpusPacketPacer, StreamingPcmToOpus


@pytest.mark.asyncio
async def test_opus_packet_pacer_keeps_60ms_spacing_without_catchup_bursts() -> None:
    now = 0.0
    sent_at: list[float] = []
    sent_packets: list[bytes] = []

    def clock() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        now += delay

    async def send(packet: bytes) -> bool:
        nonlocal now
        sent_packets.append(packet)
        sent_at.append(now)
        now += 0.01
        return True

    pacer = OpusPacketPacer(send, frame_duration_ms=60, clock=clock, sleep=sleep)
    for index in range(4):
        assert await pacer.send(bytes([index]))

    assert sent_at == pytest.approx([0.0, 0.06, 0.12, 0.18])

    now = 0.50
    assert await pacer.send(b"late")
    assert sent_at[-1] == pytest.approx(0.50)
    assert await pacer.send(b"next")
    assert sent_at[-1] == pytest.approx(0.56)
    assert sent_packets == [b"\x00", b"\x01", b"\x02", b"\x03", b"late", b"next"]


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
async def test_streaming_encoder_preserves_multi_chunk_audio_duration() -> None:
    duration_seconds = 2.4
    sample_rate = 24000
    samples = [
        int(5000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        for index in range(int(duration_seconds * sample_rate))
    ]
    pcm = b"".join(struct.pack("<h", sample) for sample in samples)
    encoder = StreamingPcmToOpus("ffmpeg")
    await encoder.start()

    async def collect() -> list[bytes]:
        return [packet async for packet in encoder.packets()]

    packet_task = asyncio.create_task(collect())
    split = len(pcm) // 2
    await encoder.write(pcm[:split])
    await encoder.write(pcm[split:])
    await encoder.finish()
    packets = await packet_task

    ogg = opus_packets_to_ogg(packets, input_sample_rate=24000, frame_duration_ms=60)
    decoder = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "-ar",
        "24000",
        "-ac",
        "1",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    decoded, error = await decoder.communicate(ogg)
    assert decoder.returncode == 0, error.decode(errors="replace")
    decoded_duration = len(decoded) / 2 / sample_rate
    assert abs(decoded_duration - duration_seconds) <= 0.06
    assert len(packets) in {40, 41}


@pytest.mark.asyncio
async def test_streaming_encoder_prebuffers_packets_before_playback() -> None:
    encoder = StreamingPcmToOpus("unused")
    packets = encoder.packets(prebuffer_packets=3)
    first_packet = asyncio.create_task(anext(packets))

    await encoder._packets.put(b"one")
    await encoder._packets.put(b"two")
    await asyncio.sleep(0)
    assert first_packet.done() is False

    await encoder._packets.put(b"three")
    assert await first_packet == b"one"
    assert await anext(packets) == b"two"
    assert await anext(packets) == b"three"
    await encoder._packets.put(None)
    with pytest.raises(StopAsyncIteration):
        await anext(packets)
