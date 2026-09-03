import asyncio
import math
import shutil
import struct
from types import SimpleNamespace
from typing import cast

import pytest

from backend.app.audio_formats import opus_packets_to_ogg
from backend.realtime.media import OpusPacketPacer, StreamingPcmToOpus
from backend.realtime.session import PLAYBACK_STARTUP_PACKETS


def test_realtime_playback_uses_five_packet_startup_buffer() -> None:
    assert PLAYBACK_STARTUP_PACKETS == 5


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
async def test_opus_packet_pacer_builds_a_startup_buffer_before_steady_pacing() -> None:
    now = 0.0
    sent_at: list[float] = []

    def clock() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        now += delay

    async def send(_: bytes) -> bool:
        nonlocal now
        sent_at.append(now)
        now += 0.01
        return True

    pacer = OpusPacketPacer(
        send,
        frame_duration_ms=60,
        startup_burst_packets=5,
        clock=clock,
        sleep=sleep,
    )
    for index in range(7):
        assert await pacer.send(bytes([index]))

    # Five 60 ms packets reach the device immediately, forming a 300 ms
    # buffer. The sixth packet starts the steady 60 ms cadence.
    assert sent_at == pytest.approx([0.0, 0.01, 0.02, 0.03, 0.04, 0.06, 0.12])


@pytest.mark.asyncio
async def test_opus_packet_pacer_reports_only_the_first_delivered_packet() -> None:
    first_send_events = 0

    async def send(_: bytes) -> bool:
        return True

    def on_first_send() -> None:
        nonlocal first_send_events
        first_send_events += 1

    pacer = OpusPacketPacer(send, on_first_send=on_first_send)

    assert await pacer.send(b"one")
    assert await pacer.send(b"two")
    assert first_send_events == 1


@pytest.mark.asyncio
async def test_opus_packet_pacer_does_not_report_a_failed_first_send() -> None:
    delivered = iter((False, True))
    first_send_events = 0

    async def send(_: bytes) -> bool:
        return next(delivered)

    def on_first_send() -> None:
        nonlocal first_send_events
        first_send_events += 1

    pacer = OpusPacketPacer(send, on_first_send=on_first_send)

    assert await pacer.send(b"failed") is False
    assert first_send_events == 0
    assert await pacer.send(b"delivered") is True
    assert first_send_events == 1


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
    decoded_samples = struct.unpack(f"<{len(decoded) // 2}h", decoded)
    rms = math.sqrt(sum(sample * sample for sample in decoded_samples) / len(decoded_samples))
    peak = max(abs(sample) for sample in decoded_samples)
    zero_crossings = sum(
        1
        for left, right in zip(decoded_samples, decoded_samples[1:], strict=False)
        if (left < 0 <= right) or (left >= 0 > right)
    )
    estimated_frequency = zero_crossings / 2 / decoded_duration
    assert rms > 1000
    assert 1000 < peak < 32767
    assert estimated_frequency == pytest.approx(440, abs=20)


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
async def test_streaming_encoder_yields_prebuffer_before_input_closes() -> None:
    sample_rate = 24000
    pcm = b"".join(
        struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * index / sample_rate)))
        for index in range(int(1.2 * sample_rate))
    )
    encoder = StreamingPcmToOpus("ffmpeg")
    await encoder.start()
    packets = encoder.packets(prebuffer_packets=PLAYBACK_STARTUP_PACKETS)
    first_packet = asyncio.create_task(anext(packets))

    try:
        await encoder.write(pcm)
        assert await asyncio.wait_for(asyncio.shield(first_packet), timeout=1.0)
    finally:
        await encoder.finish()


@pytest.mark.asyncio
async def test_streaming_encoder_bounds_raw_pcm_probe_before_opening_input(monkeypatch) -> None:
    command: tuple[object, ...] = ()

    class _EmptyStdout:
        async def read(self, _: int) -> bytes:
            return b""

    async def create_subprocess_exec(*args, **kwargs):
        nonlocal command
        del kwargs
        command = args
        return SimpleNamespace(stdout=_EmptyStdout())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_subprocess_exec)
    encoder = StreamingPcmToOpus("ffmpeg")
    await encoder.start()
    assert encoder._reader_task is not None
    await encoder._reader_task

    input_index = command.index("-i")
    probe_index = command.index("-probesize")
    assert command[probe_index + 1] == "32"
    assert probe_index < input_index


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


@pytest.mark.asyncio
async def test_streaming_encoder_flushes_short_audio_before_prebuffer_target() -> None:
    encoder = StreamingPcmToOpus("unused")
    packets = encoder.packets(prebuffer_packets=PLAYBACK_STARTUP_PACKETS)

    async def collect_packets() -> list[bytes]:
        return [packet async for packet in packets]

    collected = asyncio.create_task(
        asyncio.wait_for(
            collect_packets(),
            timeout=0.1,
        )
    )

    await encoder._packets.put(b"one")
    await encoder._packets.put(b"two")
    await encoder._packets.put(None)

    assert await collected == [b"one", b"two"]


@pytest.mark.asyncio
async def test_streaming_encoder_surfaces_reader_failure_to_packet_consumer() -> None:
    class _BrokenStdout:
        async def read(self, _: int) -> bytes:
            return b"not-an-ogg-page" * 3

    encoder = StreamingPcmToOpus("unused")
    encoder.process = cast(
        asyncio.subprocess.Process,
        SimpleNamespace(stdout=_BrokenStdout()),
    )

    with pytest.raises(RuntimeError, match="invalid Ogg stream"):
        await encoder._read_output()
    with pytest.raises(RuntimeError, match="invalid Ogg stream"):
        await asyncio.wait_for(anext(encoder.packets()), timeout=0.1)
