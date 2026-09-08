import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.config import Settings
from backend.app.models import ModelPreset, VoicePreset
from backend.app.voice_routes import QWEN_INSTRUCT_MODEL, voice_is_compatible
from backend.realtime.providers import QwenRealtimeTtsSession, RealtimeProviderError


async def test_emotion_update_is_acknowledged_deduplicated_and_reset():
    ws = AsyncMock()
    ws.recv.return_value = json.dumps({"type": "session.updated"})
    tts = QwenRealtimeTtsSession(ws)
    await tts.set_emotion("angry")
    ws.send.assert_not_called()  # Ordinary TTS must not receive unsupported instructions.
    tts.instruction_control = True
    tts._configuration = {"voice": "Vivian", "mode": "commit", "speech_rate": 1.2}
    await tts.set_emotion("angry")
    await tts.set_emotion("angry")
    assert ws.send.await_count == 1
    first = json.loads(ws.send.call_args.args[0])["session"]
    assert "生气" in first["instructions"]
    assert first["voice"] == "Vivian" and first["mode"] == "commit"
    assert first["speech_rate"] == 1.2
    with patch("backend.realtime.providers.connect", AsyncMock(return_value=ws)) as reconnect:
        await tts.set_emotion("untrusted arbitrary instruction")
    reconnect.assert_awaited_once()
    ws.close.assert_awaited_once()
    assert "平静" in json.loads(ws.send.call_args.args[0])["session"]["instructions"]
    assert ws.recv.await_count == 2


def test_instruct_voice_compatibility_rejects_other_model_voices():
    model = ModelPreset(
        route_kind="cascade", tts_provider="dashscope", tts_model=QWEN_INSTRUCT_MODEL
    )
    assert voice_is_compatible(model, VoicePreset(provider="dashscope", voice="Vivian"))
    assert not voice_is_compatible(model, VoicePreset(provider="dashscope", voice="Jennifer"))
    assert not voice_is_compatible(model, VoicePreset(provider="doubao", voice="Vivian"))


async def test_instruct_defers_its_only_session_update_until_emotion_is_known():
    ws = AsyncMock()
    ws.recv.return_value = json.dumps({"type": "session.updated"})
    settings = Settings(qwen_realtime_tts_model=QWEN_INSTRUCT_MODEL)
    with patch("backend.realtime.providers.connect", AsyncMock(return_value=ws)):
        tts = await QwenRealtimeTtsSession.open(settings, voice="Cherry", speech_rate=1.0)
        ws.send.assert_not_called()
        await tts.set_emotion("angry")
    assert ws.send.await_count == 1
    assert "生气" in json.loads(ws.send.call_args.args[0])["session"]["instructions"]


async def test_cancel_during_emotion_reconnect_closes_late_socket():
    old, late = AsyncMock(), AsyncMock()
    late.recv.return_value = json.dumps({"type": "session.updated"})
    tts = QwenRealtimeTtsSession(old)
    tts.instruction_control = True
    tts._emotion = "neutral"
    started, release = asyncio.Event(), asyncio.Event()

    async def connect_late(*args, **kwargs):
        started.set()
        await release.wait()
        return late

    with patch("backend.realtime.providers.connect", connect_late):
        task = asyncio.create_task(tts.set_emotion("angry"))
        await started.wait()
        await tts.cancel()
        release.set()
        with pytest.raises(RealtimeProviderError, match="closed"):
            await asyncio.wait_for(task, 1)
    late.close.assert_awaited_once()
    late.send.assert_not_called()
    with pytest.raises(RealtimeProviderError, match="closed"):
        await tts.set_emotion("happy")
