import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.config import Settings
from backend.app.voice_routes import QWEN_INSTRUCT_MODEL, validate_route_admission
from backend.realtime.providers import (
    QwenRealtimeAsrSession,
    QwenRealtimeTtsSession,
    RealtimeProviderError,
)


@pytest.mark.parametrize("provider,model,key,url,flag", [
    ("volc-tts", "seed-tts-2.0", "volc_tts_api_key", "volc_tts_url", "volc_tts_validated"),
    ("dashscope", QWEN_INSTRUCT_MODEL, "tts_api_key", "qwen_realtime_tts_url",
     "qwen_instruct_validated"),
])
def test_candidate_production_admission(provider, model, key, url, flag):
    settings = Settings(_env_file=None)
    route = SimpleNamespace(route_kind="cascade", tts_provider=provider, tts_model=model)
    with pytest.raises(ValueError, match="credential"):
        validate_route_admission(route, settings)
    setattr(settings, key, "test-key")
    validate_route_admission(route, settings)  # Isolated development candidate.
    settings.app_env = "production"
    with pytest.raises(ValueError, match="release validation"):
        validate_route_admission(route, settings)
    setattr(settings, flag, True)
    validate_route_admission(route, settings)
    setattr(settings, url, "http://untrusted.invalid")
    with pytest.raises(ValueError, match="HTTPS|WSS"):
        validate_route_admission(route, settings)


@pytest.mark.parametrize("limit", ["count", "bytes"])
async def test_asr_overflow_wakes_finish_and_can_cancel(limit):
    ws = AsyncMock()
    ws.recv.return_value = json.dumps({"type": "partial", "text": "x" * 60})
    asr = QwenRealtimeAsrSession(ws, buffer_events=2,
                                 buffer_bytes=100 if limit == "bytes" else 10000)
    asr._reader_task = asyncio.create_task(asr._read_events())
    await asyncio.wait_for(asr._reader_task, 1)
    assert asr.endpoint_detected() and asr._events.qsize() == 1
    assert asr._queued_bytes == 0
    with pytest.raises(RealtimeProviderError, match="buffer-overflow"):
        await asr.finish()
    await asyncio.wait_for(asr.cancel(), 1)
    ws.close.assert_awaited_once()


@pytest.mark.parametrize("limit", ["count", "bytes"])
async def test_tts_overflow_is_terminal_without_draining_stale_audio(limit):
    ws = AsyncMock()
    ws.recv.return_value = json.dumps({
        "type": "response.audio.delta", "delta": base64.b64encode(b"00" * 30).decode(),
    })
    tts = QwenRealtimeTtsSession(ws, buffer_events=2,
                                 buffer_bytes=90 if limit == "bytes" else 10000)
    with pytest.raises(RealtimeProviderError, match="buffer-overflow"):
        async with asyncio.timeout(1):
            _ = [x async for x in tts.synthesize("test")]
    await asyncio.wait_for(tts.cancel(), 1)
    assert tts.closed


def test_admin_cannot_enable_unvalidated_candidate(client, admin_headers):
    from .test_voice_routes import _staff_headers

    staff = _staff_headers(client, admin_headers)
    settings = client.app.state.settings
    settings.app_env = "production"
    settings.volc_tts_api_key = "test-key"
    settings.volc_tts_validated = False
    response = client.patch("/v1/admin/model-presets/volc-tts-chat", headers=staff,
                            json={"enabled": True, "confirm": True})
    assert response.status_code == 422
    rows = client.get("/v1/admin/model-presets", headers=staff).json()
    assert not next(row for row in rows if row["id"] == "volc-tts-chat")["enabled"]


async def test_startup_catalog_rejects_enabled_unvalidated_route(client):
    from backend.app.catalog import validate_release_catalog
    from backend.app.models import ModelPreset

    settings = Settings(_env_file=None, volc_tts_api_key="test-key")
    settings.app_env = "production"
    async with client.app.state.session_factory() as db:
        model = await db.get(ModelPreset, "volc-tts-chat")
        model.enabled = True
        await db.flush()
        with pytest.raises(ValueError, match="release validation"):
            await validate_release_catalog(db, settings)


async def test_full_tts_queue_still_delivers_completion():
    ws = AsyncMock()
    frame = {"type": "response.audio.delta", "delta": base64.b64encode(b"00").decode()}
    ws.recv.side_effect = [json.dumps(frame), json.dumps(frame),
                           json.dumps({"type": "response.done"})]
    tts = QwenRealtimeTtsSession(ws, buffer_events=2, buffer_bytes=4)
    async with asyncio.timeout(1):
        assert b"".join([x async for x in tts.synthesize("test")]) == b"0000"
    await tts.cancel()
