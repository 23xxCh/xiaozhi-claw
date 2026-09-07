"""Real FFmpeg cancellation checks under downstream backpressure."""

import asyncio
import contextlib
import shutil
import time

import pytest

from backend.realtime.media import StreamingPcmToOpus

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")


async def test_encoder_cancel_reaps_process_with_full_queue_and_no_consumer() -> None:
    encoder = StreamingPcmToOpus("ffmpeg")
    await encoder.start()
    writer = asyncio.create_task(encoder.write(b"\0\0" * 24000 * 30))
    try:
        async with asyncio.timeout(5):
            while not encoder._packets.full():
                await asyncio.sleep(0.01)
        assert encoder._reader_task is not None and not encoder._reader_task.done()
        started = time.monotonic()
        await asyncio.wait_for(encoder.cancel(), timeout=1.5)
        assert time.monotonic() - started < 1.5
        assert encoder.process is not None and encoder.process.returncode is not None
        assert encoder._reader_task.done()
        await asyncio.wait_for(encoder.process.wait(), timeout=0.2)
        assert [packet async for packet in encoder.packets()] == []
        await encoder.cancel()
        with pytest.raises(RuntimeError, match="not running"):
            await encoder.write(b"\0\0")
    finally:
        # This also reaps the broken pre-fix encoder when the regression times out.
        tasks = [writer]
        if encoder._reader_task is not None:
            tasks.append(encoder._reader_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if encoder.process is not None:
            if encoder.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    encoder.process.kill()
            await asyncio.wait_for(encoder.process.communicate(), timeout=2)


async def test_encoder_cancel_releases_a_waiting_packet_consumer() -> None:
    encoder = StreamingPcmToOpus("ffmpeg")
    await encoder.start()

    async def collect():
        return [packet async for packet in encoder.packets(prebuffer_packets=5)]

    consumer = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(encoder.cancel(), timeout=1.5)
        assert await asyncio.wait_for(consumer, timeout=0.2) == []
        assert encoder.process is not None and encoder.process.returncode is not None
        assert encoder._reader_task is not None and encoder._reader_task.done()
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await encoder.cancel()
