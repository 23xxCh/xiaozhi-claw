import asyncio
import math
import shutil
import struct

import pytest

from backend.app.audio_formats import IncrementalOggOpusMuxer, ogg_opus_packets
from backend.realtime.media import StreamingOpusToPcm

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")


async def _ffmpeg(data: bytes, *arguments: str) -> bytes:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        *arguments,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, error = await asyncio.wait_for(process.communicate(data), timeout=10)
        assert process.returncode == 0, error.decode(errors="replace")
        return output
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


async def _encoded_audio() -> tuple[bytes, bytes, list[bytes]]:
    # Audible content at both ends exposes lost initial/final samples. The swept
    # tone also catches timing shifts that a repeating single-frequency tone hides.
    pcm = b"".join(
        struct.pack("<h", int(7000 * math.sin(2 * math.pi * (330 * t + 120 * t * t))))
        for index in range(19200)
        for t in (index / 16000,)
    )
    ogg = await _ffmpeg(
        pcm,
        "-f",
        "s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
        "-frame_duration",
        "60",
        "-f",
        "ogg",
        "pipe:1",
    )
    return pcm, ogg, ogg_opus_packets(ogg)


async def _collect(decoder: StreamingOpusToPcm) -> list[bytes]:
    return [chunk async for chunk in decoder.chunks()]


async def test_streaming_decoder_preserves_first_last_samples_and_20ms_boundaries() -> None:
    pcm, original_ogg, packets = await _encoded_audio()
    reference = await _ffmpeg(
        original_ogg,
        "-i",
        "pipe:0",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    )
    opus_head = original_ogg.index(b"OpusHead")
    pre_skip_48k = struct.unpack_from("<H", original_ogg, opus_head + 10)[0]
    raw_muxer = IncrementalOggOpusMuxer(
        input_sample_rate=16000, frame_duration_ms=60, pre_skip_samples=0
    )
    raw_reference = await _ffmpeg(
        b"".join(raw_muxer.add_packet(packet) for packet in packets),
        "-i",
        "pipe:0",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    )
    decoder = StreamingOpusToPcm("ffmpeg")
    await decoder.start()
    consumer = asyncio.create_task(_collect(decoder))
    try:
        for packet in packets:
            await decoder.write(packet)
        await decoder.finish()
        chunks = await asyncio.wait_for(consumer, timeout=2)
        decoded = b"".join(chunks)
        # Device raw packets do not carry the original container's pre-skip or
        # final granule trim. Keep ALL packet samples, including codec padding.
        assert len(decoded) == len(packets) * 960 * 2
        assert all(len(chunk) == 640 for chunk in chunks)
        assert decoded == raw_reference
        skip_bytes = pre_skip_48k // 3 * 2
        aligned = decoded[skip_bytes : skip_bytes + len(reference)]
        assert len(reference) == len(pcm)
        # FFmpeg's resampler has different boundary padding when pre-skip and
        # final granule trimming happen before resampling. Interior samples match
        # exactly; both 20 ms edge windows must retain the actual input signal.
        assert aligned[640:-640] == reference[640:-640]
        for actual, expected in (
            (aligned[:640], reference[:640]),
            (aligned[-640:], reference[-640:]),
        ):
            left, right = struct.unpack("<320h", actual), struct.unpack("<320h", expected)
            correlation = sum(x * y for x, y in zip(left, right, strict=True)) / math.sqrt(
                sum(x * x for x in left) * sum(y * y for y in right)
            )
            assert correlation > 0.999
        assert decoder.process is not None and decoder.process.returncode == 0
    finally:
        await decoder.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


async def test_streaming_decoder_produces_pcm_before_input_eof() -> None:
    _, _, packets = await _encoded_audio()
    decoder = StreamingOpusToPcm("ffmpeg")
    await decoder.start()
    chunks = decoder.chunks()
    first = asyncio.create_task(anext(chunks))
    try:
        for packet in packets[:4]:
            await decoder.write(packet)
        assert len(await asyncio.wait_for(asyncio.shield(first), timeout=2)) == 640
        assert decoder.process is not None and decoder.process.returncode is None
    finally:
        await decoder.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await chunks.aclose()


async def test_streaming_decoder_cancel_reaps_process_when_pcm_queue_is_full() -> None:
    _, _, packets = await _encoded_audio()
    decoder = StreamingOpusToPcm("ffmpeg")
    await decoder.start()
    try:
        for packet in packets:
            await decoder.write(packet)
        async with asyncio.timeout(3):
            while not decoder._chunks.full():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(decoder.cancel(), timeout=3)
        assert decoder.process is not None and decoder.process.returncode is not None
        assert decoder._reader_task is not None and decoder._reader_task.done()
        assert decoder._stderr_task is not None and decoder._stderr_task.done()
        assert await asyncio.wait_for(_collect(decoder), timeout=1) == []
        await decoder.cancel()
        with pytest.raises(RuntimeError, match="not open"):
            await decoder.write(packets[0])
    finally:
        await decoder.cancel()


async def test_streaming_decoder_cancel_unblocks_waiting_consumer() -> None:
    decoder = StreamingOpusToPcm("ffmpeg")
    await decoder.start()
    consumer = asyncio.create_task(_collect(decoder))
    await decoder.cancel()
    assert await asyncio.wait_for(consumer, timeout=1) == []
    assert decoder.process is not None and decoder.process.returncode is not None


async def test_cancelling_finish_with_backpressure_reaps_decoder() -> None:
    _, _, packets = await _encoded_audio()
    decoder = StreamingOpusToPcm("ffmpeg")
    await decoder.start()
    try:
        for packet in packets:
            await decoder.write(packet)
        async with asyncio.timeout(3):
            while not decoder._chunks.full():
                await asyncio.sleep(0.01)
        finishing = asyncio.create_task(decoder.finish())
        await asyncio.sleep(0)
        finishing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(finishing, timeout=3)
        assert decoder.process is not None and decoder.process.returncode is not None
        assert decoder._reader_task is not None and decoder._reader_task.done()
    finally:
        await decoder.cancel()


async def test_streaming_decoder_surfaces_invalid_opus_and_reaps_process() -> None:
    decoder = StreamingOpusToPcm("ffmpeg")
    await decoder.start()
    consumer = asyncio.create_task(_collect(decoder))
    try:
        await decoder.write(b"\xff")
        with pytest.raises(RuntimeError, match="decoder failed"):
            await decoder.finish()
        with pytest.raises(RuntimeError, match="decoder failed"):
            await consumer
        assert decoder.process is not None and decoder.process.returncode is not None
    finally:
        await decoder.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


def test_muxer_pre_skip_is_explicit_without_changing_existing_default() -> None:
    for pre_skip in (0, 312):
        muxer = IncrementalOggOpusMuxer(
            input_sample_rate=16000, frame_duration_ms=60, pre_skip_samples=pre_skip
        )
        data = muxer.add_packet(b"packet")
        assert struct.unpack_from("<H", data, data.index(b"OpusHead") + 10)[0] == pre_skip
    default = IncrementalOggOpusMuxer(input_sample_rate=16000, frame_duration_ms=60)
    assert default.pre_skip_samples == 312
