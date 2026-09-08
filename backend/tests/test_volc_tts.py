import base64
import json

import httpx
import pytest

from backend.app.config import Settings
from backend.app.models import ModelPreset, VoicePreset
from backend.app.voice_routes import voice_is_compatible
from backend.realtime.providers import RealtimeProviderError, VolcTtsSession


@pytest.mark.parametrize("case", ["ok", "truncated", "empty", "provider-error", "http-error"])
async def test_sse_completion_and_errors(case):
    events = [{"code": 0, "data": base64.b64encode(b"\x00\x01" * 20).decode()}]
    if case == "empty":
        events = []
    if case == "provider-error":
        events = [{"code": 55000000, "message": "private provider detail"}]
    if case != "truncated":
        events.append({"code": 20000000})

    def handle(request):
        assert request.headers["X-Api-Resource-Id"] == "seed-tts-2.0"
        body = json.loads(request.content)["req_params"]
        assert body["audio_params"] == {
            "format": "pcm", "sample_rate": 24000, "speech_rate": 20,
        }
        return httpx.Response(
            403 if case == "http-error" else 200,
            text="".join("data: " + json.dumps(e) + "\n\n" for e in events),
        )

    tts = VolcTtsSession(Settings(volc_tts_api_key="test"), "voice", 1.2)
    await tts.client.aclose()
    tts.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        if case == "ok":
            assert b"".join([x async for x in tts.synthesize("你好")]) == b"\x00\x01" * 20
        else:
            with pytest.raises(RealtimeProviderError) as exc:
                _ = [x async for x in tts.synthesize("你好")]
            assert "private provider detail" not in str(exc.value)
    finally:
        await tts.cancel()
    assert tts.client.is_closed
    with pytest.raises(RealtimeProviderError, match="closed"):
        _ = [x async for x in tts.synthesize("迟到文本")]


def test_volc_voices_do_not_cross_routes():
    model = ModelPreset(route_kind="cascade", tts_provider="volc-tts", tts_model="seed-tts-2.0")
    assert voice_is_compatible(model, VoicePreset(
        provider="volc-tts", voice="zh_female_vv_uranus_bigtts"))
    assert not voice_is_compatible(model, VoicePreset(provider="dashscope", voice="Cherry"))
    assert not voice_is_compatible(model, VoicePreset(
        provider="doubao", voice="zh_female_vv_jupiter_bigtts"))
    assert not voice_is_compatible(model, VoicePreset(provider="volc-tts", voice="unknown"))
