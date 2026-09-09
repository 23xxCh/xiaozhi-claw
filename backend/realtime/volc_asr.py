"""Volc streaming ASR: bounded binary protocol, 180 ms Ogg/Opus uploads."""

import asyncio
import gzip
import io
import json
import struct
import time
import uuid

from websockets.asyncio.client import connect

from backend.app.audio_formats import IncrementalOggOpusMuxer
from .providers import RealtimeProviderError, TranscriptionResult


def request_packet(payload: bytes, *, audio=False, final=False) -> bytes:
    payload = gzip.compress(payload)
    return bytes((0x11, (0x20 if audio else 0x10) | (2 if final else 0),
                  0x01 if audio else 0x11, 0)) + struct.pack('>I', len(payload)) + payload


def parse_response(raw: bytes) -> tuple[dict, bool]:
    if not isinstance(raw, bytes) or len(raw) < 8 or raw[0] != 0x11:
        raise RealtimeProviderError('volc-asr', 'invalid-header')
    kind, flags = raw[1] >> 4, raw[1] & 15
    offset = 4
    if kind == 15:
        raise RealtimeProviderError('volc-asr', str(struct.unpack_from('>I', raw, 4)[0]))
    if kind != 9 or flags not in (0, 1, 2, 3) or raw[2] not in (0x10, 0x11):
        raise RealtimeProviderError('volc-asr', 'invalid-response')
    if flags & 1:
        offset += 4
    if len(raw) < offset+4:
        raise RealtimeProviderError('volc-asr', 'truncated-response')
    size = struct.unpack_from('>I', raw, offset)[0]
    payload = raw[offset+4:]
    if size != len(payload) or size > 1024*1024:
        raise RealtimeProviderError('volc-asr', 'invalid-size')
    if raw[2] & 1:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            payload = stream.read(1024*1024+1)
    if len(payload) > 1024*1024:
        raise RealtimeProviderError('volc-asr', 'response-too-large')
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RealtimeProviderError('volc-asr', 'invalid-json')
    return data, bool(flags & 2)


class VolcAsrSession:
    def __init__(self, ws, timeout):
        self.ws, self.timeout = ws, timeout
        self.mux = IncrementalOggOpusMuxer(input_sample_rate=16000, frame_duration_ms=60)
        self.pending = bytearray()
        self.frames = 0
        self.closed = False
        self.ended = False
        self._endpoint = asyncio.Event()
        self.reader = asyncio.create_task(self._receive())

    @classmethod
    async def open(cls, settings):
        ws = await connect(settings.volc_asr_url, proxy=None, additional_headers={
            'X-Api-Key': settings.volc_asr_api_key,
            'X-Api-Resource-Id': 'volc.bigasr.sauc.duration',
            'X-Api-Request-Id': str(uuid.uuid4()),
        }, open_timeout=settings.provider_timeout_seconds, close_timeout=2, max_size=1024*1024)
        try:
            payload = {'user': {'uid': uuid.uuid4().hex},
                       'audio': {'format': 'ogg', 'codec': 'opus', 'rate': 16000, 'bits': 16, 'channel': 1},
                       'request': {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True}}
            await ws.send(request_packet(json.dumps(payload).encode()))
            async with asyncio.timeout(settings.provider_timeout_seconds):
                parse_response(await ws.recv())
            return cls(ws, settings.provider_timeout_seconds)
        except BaseException:
            await ws.close()
            raise

    async def _receive(self):
        try:
            while True:
                data, final = parse_response(await self.ws.recv())
                if final:
                    text = data.get('result', {}).get('text', '')
                    if not isinstance(text, str):
                        raise RealtimeProviderError('volc-asr', 'invalid-text')
                    return TranscriptionResult(text.strip(), transcription_completed_at=time.perf_counter())
        finally:
            self._endpoint.set()

    def endpoint_detected(self):
        return self._endpoint.is_set()

    async def send_audio(self, frame):
        if self.closed or self.ended:
            raise RealtimeProviderError('volc-asr', 'input-closed')
        self.pending.extend(self.mux.add_packet(frame))
        self.frames += 1
        if self.frames % 3 == 0:
            async with asyncio.timeout(self.timeout):
                await self.ws.send(request_packet(bytes(self.pending), audio=True))
            self.pending.clear()

    async def finish(self):
        try:
            async with asyncio.timeout(self.timeout):
                if not self.ended:
                    self.ended = True
                    await self.ws.send(request_packet(bytes(self.pending), audio=True, final=True))
                    self.pending.clear()
                return await self.reader
        finally:
            await self.cancel()

    async def cancel(self):
        if self.closed:
            return
        self.closed = True
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        await self.ws.close()
