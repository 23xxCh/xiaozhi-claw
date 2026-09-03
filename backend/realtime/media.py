import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable


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
        if self.process is None or self.process.stdin is None:
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
            if packet is None:
                for buffered_packet in buffered:
                    yield buffered_packet
                return
            if isinstance(packet, BaseException):
                raise packet
            buffered.append(packet)
        for buffered_packet in buffered:
            yield buffered_packet
        while True:
            packet = await self._packets.get()
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
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            with contextlib.suppress(ProcessLookupError):
                await self.process.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
