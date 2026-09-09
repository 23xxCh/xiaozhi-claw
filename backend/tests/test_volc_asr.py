import asyncio
import gzip
import json
import struct
from unittest.mock import AsyncMock

import pytest

from backend.app.config import Settings
from backend.realtime import volc_asr
from backend.realtime.providers import RealtimeProviderError


def response(data, flags=1):
    payload = gzip.compress(json.dumps(data).encode())
    return (
        bytes((0x11, 0x90 | flags, 0x11, 0))
        + struct.pack(">iI", -1 if flags == 3 else 1, len(payload))
        + payload
    )


async def test_upload_grouping_final_and_cleanup(monkeypatch):
    queue = asyncio.Queue()
    queue.put_nowait(response({}))
    ws = AsyncMock()
    ws.recv.side_effect = queue.get
    monkeypatch.setattr(volc_asr, "connect", AsyncMock(return_value=ws))
    session = await volc_asr.VolcAsrSession.open(Settings(volc_asr_api_key="test"))
    for _ in range(4):
        await session.send_audio(b"\xf8\xff\xfe")
    assert ws.send.await_count == 2
    queue.put_nowait(response({"result": {"text": "你好"}}, flags=3))
    result = await session.finish()
    assert result.text == "你好"
    assert ws.send.call_args.args[0][1] == 0x22
    assert session.reader.done()
    ws.close.assert_awaited_once()


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x11\x91\x11\0",
        response({})[:-1],
        response({}) + b"x",
        response({}).replace(b"\x11\x91", b"\x11\xa1", 1),
    ],
)
def test_reject_invalid_protocol(raw):
    with pytest.raises(RealtimeProviderError):
        volc_asr.parse_response(raw)


def test_bounded_decompression():
    with pytest.raises(RealtimeProviderError, match="response-too-large"):
        volc_asr.parse_response(response({"text": "a" * (1024 * 1024)}))
