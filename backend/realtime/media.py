import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable

from backend.app.audio_formats import IncrementalOggOpusMuxer


async def _stop_ffmpeg(process: asyncio.subprocess.Process) -> None:
    """Call after stopping readers; drain pipes while waiting for bounded shutdown."""
    if process.stdin is not None:
        process.stdin.close()
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.communicate(), timeout=1)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await asyncio.wait_for(process.communicate(), timeout=1)


class StreamingOpusToPcm:
    """Decode device 16 kHz mono / 60 ms raw Opus into 20 ms PCM chunks.

    Consume chunks() concurrently with write()/finish() to allow backpressure.
    This is one decoder per input turn; cancel() also releases blocked consumers.
    Raw device packets have no container pre-skip metadata, and the device encoder
    persists across turns. Do not invent a new encoder delay to trim every turn.
    """

    def __init__(self, ffmpeg_path: str) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.process: asyncio.subprocess.Process | None = None
        self._muxer = IncrementalOggOpusMuxer(
            input_sample_rate=16000, frame_duration_ms=60, pre_skip_samples=0
        )
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=50)
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._error: Exception | None = None
        self._stderr = bytearray()
        self._input_closed = False
        self._cancelled = False
        self._wrote_audio = False

    async def start(self) -> None:
        if self.process is not None or self._input_closed:
            raise RuntimeError("decoder cannot be restarted")
        self.process = await asyncio.create_subprocess_exec(
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-probesize",
            "32",
            "-analyzeduration",
            "0",
            "-f",
            "ogg",
            "-i",
            "pipe:0",
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "s16le",
            "-flush_packets",
            "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=6400,
        )
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._reader_task = asyncio.create_task(self._read_output())

    async def write(self, packet: bytes) -> None:
        if self.process is None or self.process.stdin is None or self._input_closed:
            raise RuntimeError("decoder input is not open")
        if self._error is not None:
            raise self._error
        self.process.stdin.write(self._muxer.add_packet(packet))
        await self.process.stdin.drain()
        self._wrote_audio = True

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        while data := await self.process.stderr.read(4096):
            self._stderr.extend(data)
            del self._stderr[:-4096]

    async def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                try:
                    pcm = await self.process.stdout.readexactly(640)
                except asyncio.IncompleteReadError as exc:
                    if len(exc.partial) % 2:
                        raise RuntimeError("decoder returned a truncated PCM sample") from exc
                    if exc.partial:
                        await self._chunks.put(exc.partial)
                    break
                await self._chunks.put(pcm)
            return_code = await self.process.wait()
            if self._stderr_task is not None:
                await self._stderr_task
            if return_code != 0 and not self._cancelled and self._wrote_audio:
                raise RuntimeError(
                    "FFmpeg streaming decoder failed: "
                    + self._stderr.decode(errors="replace").strip()
                )
        except Exception as exc:
            self._error = exc
        finally:
            if not self._cancelled:
                await self._chunks.put(None)

    async def chunks(self):
        while True:
            pcm = await self._chunks.get()
            if pcm is None:
                if self._error is not None:
                    raise self._error
                return
            yield pcm

    async def finish(self) -> None:
        """Close the Ogg input at EOF and flush every decoded sample, without padding."""
        if self.process is None or self._cancelled:
            return
        try:
            async with asyncio.timeout(5):
                if not self._input_closed:
                    self._input_closed = True
                    assert self.process.stdin is not None
                    self.process.stdin.close()
                    await self.process.stdin.wait_closed()
                if self._reader_task is not None:
                    await asyncio.shield(self._reader_task)
                if self._error is not None:
                    raise self._error
        except BaseException:
            await self.cancel()
            raise

    async def cancel(self) -> None:
        """Discard queued PCM, stop the subprocess and release its pipe readers."""
        if self._cancelled:
            return
        self._cancelled = True
        self._input_closed = True
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._reader_task, self._stderr_task) if task is not None),
            return_exceptions=True,
        )
        try:
            if self.process is not None:
                await _stop_ffmpeg(self.process)
        finally:
            while not self._chunks.empty():
                self._chunks.get_nowait()
            self._chunks.put_nowait(None)


class OpusPacketPacer:
    """Send Opus frames at playback rate with a bounded startup jitter buffer."""

    def __init__(
        self,
        send: Callable[[bytes], Awaitable[bool]],
        *,
        frame_duration_ms: int = 60,
        startup_burst_packets: int = 1,
        on_first_send: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if startup_burst_packets < 1:
            raise ValueError("startup_burst_packets must be at least one")
        self._send = send
        self._frame_seconds = frame_duration_ms / 1000
        self._startup_burst_packets = startup_burst_packets
        self._on_first_send = on_first_send
        self._clock = clock
        self._sleep = sleep
        self._first_send_at: float | None = None
        self._sent_packets = 0
        self._first_delivery_reported = False

    async def send(self, packet: bytes) -> bool:
        now = self._clock()
        if self._first_send_at is None:
            self._first_send_at = now
        packet_index = self._sent_packets + 1
        target = self._first_send_at + max(
            0, packet_index - self._startup_burst_packets
        ) * self._frame_seconds
        if now < target:
            await self._sleep(target - now)
        elif now > target and packet_index > self._startup_burst_packets:
            # Keep the new schedule anchored at the actual send time. We never
            # emit catch-up bursts after the startup buffer has been filled.
            self._first_send_at = now - max(
                0, packet_index - self._startup_burst_packets
            ) * self._frame_seconds
        delivered = await self._send(packet)
        if delivered and not self._first_delivery_reported and self._on_first_send is not None:
            self._first_delivery_reported = True
            self._on_first_send()
        self._sent_packets = packet_index
        return delivered


class StreamingPcmToOpus:
    """One FFmpeg encoder per reply, preserving 60 ms Opus packet boundaries."""

    def __init__(self, ffmpeg_path: str) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._packets: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(maxsize=100)
        self._buffer = bytearray()
        self._pending_packet = bytearray()
        self._cancelled = False

    async def start(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-probesize",
            "32",
            "-f",
            "s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-vn",
            "-c:a",
            "libopus",
            "-b:a",
            "24k",
            "-frame_duration",
            "60",
            "-flush_packets",
            "1",
            "-page_duration",
            "60000",
            "-f",
            "ogg",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_output())

    async def write(self, pcm: bytes) -> None:
        if self.process is None or self.process.stdin is None or self._cancelled:
            raise RuntimeError("encoder is not running")
        self.process.stdin.write(pcm)
        await self.process.stdin.drain()

    async def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while chunk := await self.process.stdout.read(4096):
                self._buffer.extend(chunk)
                await self._extract_pages()
        except Exception as exc:
            await self._packets.put(exc)
            raise
        else:
            await self._packets.put(None)

    async def _extract_pages(self) -> None:
        while len(self._buffer) >= 27:
            if self._buffer[:4] != b"OggS":
                raise RuntimeError("FFmpeg returned an invalid Ogg stream")
            segment_count = self._buffer[26]
            if len(self._buffer) < 27 + segment_count:
                return
            lacing = self._buffer[27 : 27 + segment_count]
            page_size = 27 + segment_count + sum(lacing)
            if len(self._buffer) < page_size:
                return
            payload_offset = 27 + segment_count
            for segment_size in lacing:
                self._pending_packet.extend(
                    self._buffer[payload_offset : payload_offset + segment_size]
                )
                payload_offset += segment_size
                if segment_size < 255:
                    packet = bytes(self._pending_packet)
                    self._pending_packet.clear()
                    if not packet.startswith((b"OpusHead", b"OpusTags")):
                        await self._packets.put(packet)
            del self._buffer[:page_size]

    async def packets(self, *, prebuffer_packets: int = 0):
        buffered: list[bytes] = []
        while len(buffered) < prebuffer_packets:
            packet = await self._packets.get()
            if self._cancelled:
                return
            if packet is None:
                for buffered_packet in buffered:
                    yield buffered_packet
                return
            if isinstance(packet, BaseException):
                raise packet
            buffered.append(packet)
        for buffered_packet in buffered:
            if self._cancelled:
                return
            yield buffered_packet
        while True:
            packet = await self._packets.get()
            if self._cancelled:
                return
            if packet is None:
                return
            if isinstance(packet, BaseException):
                raise packet
            yield packet

    async def finish(self) -> None:
        if self.process is None:
            return
        if self.process.stdin is not None:
            self.process.stdin.close()
            await self.process.stdin.wait_closed()
        return_code = await self.process.wait()
        if self._reader_task is not None:
            await self._reader_task
        if return_code != 0:
            error = b""
            if self.process.stderr is not None:
                error = await self.process.stderr.read()
            raise RuntimeError(
                f"FFmpeg streaming encoder failed: {error.decode(errors='replace').strip()}"
            )

    async def cancel(self) -> None:
        """Discard buffered packets and reap FFmpeg even without a packet consumer."""
        if self._cancelled:
            return
        self._cancelled = True
        # A reader blocked on queue.put cannot drain stdout. Waiting for process
        # exit first can deadlock even after FFmpeg has terminated.
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        try:
            if self.process is not None:
                await _stop_ffmpeg(self.process)
        finally:
            self._buffer.clear()
            self._pending_packet.clear()
            while not self._packets.empty():
                self._packets.get_nowait()
            self._packets.put_nowait(None)
