import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import Agent, ConversationSession, Device, ProviderUsage, UsageProfile
from backend.realtime.emotion import EmotionRouter
from backend.realtime.providers import TranscriptionResult

from .conftest import provision_owned_device


def test_openapi_contains_stable_customer_and_device_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/v1/auth/wechat/start",
        "/v1/agents",
        "/v1/agents/{agent_id}/devices/{device_id}",
        "/v1/model-presets",
        "/v1/voice-presets",
        "/v1/devices",
        "/v1/claims/confirm",
        "/v1/conversations",
        "/v1/account/usage",
        "/v1/device/bootstrap",
        "/v1/ota/check",
        "/v1/device/vision/capability",
    }
    assert expected <= set(paths)


def test_committed_openapi_contract_matches_control_plane(client: TestClient) -> None:
    contract = json.loads(Path("docs/openapi.json").read_text(encoding="utf-8"))
    assert contract == client.app.openapi()


def test_public_model_presets_never_expose_provider_routing(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    response = client.get(
        "/v1/model-presets",
        headers={"Authorization": f"Bearer {owned['user_token']}"},
    )
    assert response.status_code == 200
    assert response.json()
    assert "asr_provider" not in response.json()[0]
    assert "llm_model" not in response.json()[0]


def test_conversation_and_provider_usage_models_have_no_transcript_columns() -> None:
    conversation_columns = set(ConversationSession.__table__.columns.keys())
    usage_columns = set(ProviderUsage.__table__.columns.keys())
    forbidden = {"audio", "transcript", "prompt", "reply", "conversation_text"}
    assert forbidden.isdisjoint(conversation_columns)
    assert forbidden.isdisjoint(usage_columns)


def test_all_qwen_user_emotions_and_unknown_values_have_stable_routes() -> None:
    router = EmotionRouter()
    emotions = {"surprised", "neutral", "happy", "sad", "disgusted", "angry", "fearful"}
    for emotion in emotions:
        decision = router.route(emotion, None)
        assert decision.user_emotion == emotion
        assert decision.thinking_emotion
        assert decision.reply_emotion
    assert router.route("future-emotion", None).user_emotion == "neutral"
    assert router.route("happy", "scam").reply_emotion == "safe_block"


class ImmediateAsr:
    async def send_audio(self, frame: bytes) -> None:
        del frame

    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult("测试打断", "neutral")

    async def cancel(self) -> None:
        return None


class FailingAsr(ImmediateAsr):
    async def finish(self) -> TranscriptionResult:
        raise TimeoutError("realtime ASR unavailable")


class SlowLlm:
    async def reply_stream(
        self,
        transcript: str,
        history: list[dict[str, str]],
        memories: list[str],
        *,
        system_prompt: str,
        model: str,
    ) -> AsyncIterator[str]:
        del transcript, history, memories, system_prompt, model
        await asyncio.sleep(60)
        yield "不应到达"


class SlowProviders:
    mock = True
    llm = SlowLlm()

    async def open_asr(self) -> ImmediateAsr:
        return ImmediateAsr()

    async def open_tts(self, voice: str):
        del voice
        raise AssertionError("TTS should not start before abort")


class FallbackExerciseProviders:
    mock = True

    class Llm:
        async def reply_stream(self, *args, **kwargs) -> AsyncIterator[str]:
            del args, kwargs
            yield "备用链路成功。"

    class Tts:
        async def synthesize(self, text: str) -> AsyncIterator[bytes]:
            yield text.encode()

        async def finish(self) -> None:
            return None

        async def cancel(self) -> None:
            return None

    llm = Llm()

    async def open_asr(self) -> FailingAsr:
        return FailingAsr()

    async def open_tts(self, voice: str) -> Tts:
        del voice
        return self.Tts()


def test_abort_cancels_an_inflight_llm_turn(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = SlowProviders()
    owned = provision_owned_device(client, admin_headers)
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        websocket.receive_json()
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})
        assert websocket.receive_json()["type"] == "stt"
        assert websocket.receive_json()["emotion"] == "thinking"
        websocket.send_json({"type": "abort"})
        assert websocket.receive_json()["state"] == "aborted"
        assert websocket.receive_json()["emotion"] == "interrupted"


def test_realtime_asr_failure_uses_bounded_batch_fallback(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = FallbackExerciseProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-FALLBACK-1")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("备用识别".encode())
        websocket.send_json({"type": "listen", "state": "stop"})
        stt = websocket.receive_json()
        assert stt["type"] == "stt"
        assert stt["text"] == "备用识别"
        assert stt["emotion"] == "neutral"
        assert websocket.receive_json()["type"] == "llm"
        assert websocket.receive_json()["type"] == "llm"
        assert websocket.receive_json()["state"] == "start"
        assert websocket.receive_json()["state"] == "sentence_start"
        websocket.receive_bytes()
        assert websocket.receive_json()["state"] == "stop"

    async def load_asr_usage() -> ProviderUsage | None:
        async with client.app.state.session_factory() as session:
            return await session.scalar(
                select(ProviderUsage)
                .where(ProviderUsage.operation == "asr")
                .order_by(ProviderUsage.created_at.desc())
            )

    usage = asyncio.run(load_asr_usage())
    assert usage is not None
    assert usage.error_code == "fallback-batch"


def test_first_audio_frame_can_implicitly_start_listening(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-IMPLICIT-LISTEN")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_bytes("隐式开始".encode())
        websocket.send_json({"type": "listen", "state": "stop"})
        stt = websocket.receive_json()
        assert stt["type"] == "stt"
        assert stt["text"] == "隐式开始"


def test_device_audio_buffer_has_a_configured_frame_limit(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-AUDIO-LIMIT")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        for _ in range(client.app.state.settings.max_device_audio_queue_frames + 1):
            websocket.send_bytes(b"frame")
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "audio-frame-limit"


def test_youth_policy_block_speaks_fixed_message_without_llm(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-YOUTH-POLICY")
    client.app.state.settings.family_mode_enabled = True

    async def configure_youth_profile() -> None:
        async with client.app.state.session_factory() as session:
            device = await session.get(Device, owned["device_id"])
            assert device is not None and device.active_agent_id is not None
            adult_agent = await session.get(Agent, device.active_agent_id)
            assert adult_agent is not None
            profile = UsageProfile(
                owner_user_id=device.owner_user_id,
                kind="youth",
                display_name="安静时段测试",
                age_band="14_17",
                quiet_start_minute=0,
                quiet_end_minute=1439,
            )
            session.add(profile)
            await session.flush()
            youth_agent = Agent(
                owner_user_id=device.owner_user_id,
                usage_profile_id=profile.id,
                name="家庭助手",
                system_prompt=adult_agent.system_prompt,
                model_preset_id=adult_agent.model_preset_id,
                voice_preset_id=adult_agent.voice_preset_id,
            )
            session.add(youth_agent)
            await session.flush()
            device.active_profile_id = profile.id
            device.active_agent_id = youth_agent.id
            await session.commit()

    asyncio.run(configure_youth_profile())
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        assert websocket.receive_json()["status"] == "quiet-hours"
        emotion = websocket.receive_json()
        assert emotion["type"] == "llm"
        assert emotion["emotion"] == "safe_block"
        started = websocket.receive_json()
        assert started["type"] == "tts"
        assert started["state"] == "start"
        sentence = websocket.receive_json()
        assert sentence["state"] == "sentence_start"
        assert "休息时段" in sentence["text"]
        assert websocket.receive_bytes()
        stopped = websocket.receive_json()
        assert stopped["type"] == "tts"
        assert stopped["state"] == "stop"
