import asyncio
import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.ai.context import LlmRequest
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import Agent, ConversationSession, Device, ProviderUsage, UsageProfile
from backend.realtime import session as realtime_session
from backend.realtime.emotion import EmotionRouter
from backend.realtime.providers import TranscriptionResult
from backend.realtime.session import VoiceTurnTimeline

from .conftest import provision_owned_device

ROOT = Path(__file__).resolve().parents[2]


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
    contract = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
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
    assert router.route("neutral", None).reply_emotion == "neutral"
    assert router.route("happy", "scam").reply_emotion == "safe_block"


def test_voice_turn_timeline_keeps_first_mark_and_rejects_stale_device_stage() -> None:
    now = 100.0

    def clock() -> float:
        return now

    timeline = VoiceTurnTimeline(turn_id="turn-current", started_at=100.0, clock=clock)
    now = 100.4
    timeline.mark("llm_first_token")
    now = 100.8
    timeline.mark("llm_first_token")
    timeline.bind_reply("reply-current")

    assert (
        timeline.mark_device_stage(
            "speaker_pcm_started",
            turn_id="turn-stale",
            reply_id="reply-current",
        )
        is False
    )
    assert (
        timeline.mark_device_stage(
            "speaker_pcm_started",
            turn_id="turn-current",
            reply_id="reply-stale",
        )
        is False
    )
    assert timeline.elapsed_ms("device_speaker_started") is None

    assert (
        timeline.mark_device_stage(
            "speaker_pcm_started",
            turn_id="turn-current",
            reply_id="reply-current",
        )
        is True
    )
    assert timeline.elapsed_ms("llm_first_token") == 400
    assert timeline.elapsed_ms("device_speaker_started") == 800

    record = timeline.as_record(
        serial="HENSUN-TIMELINE",
        conversation_id="conversation-current",
        outcome="completed",
        error_code=None,
        fallback_operations={"tts"},
    )
    assert record == {
        "event": "voice_turn_outcome",
        "schema_version": 1,
        "serial": "HENSUN-TIMELINE",
        "conversation_id": "conversation-current",
        "turn_id": "turn-current",
        "reply_id": "reply-current",
        "outcome": "completed",
        "error_code": None,
        "fallback_operations": ["tts"],
        "asr_transcription_completed_ms": None,
        "asr_session_finished_ms": None,
        "llm_first_token_ms": 400,
        "first_speakable_text_ms": None,
        "tts_connected_ms": None,
        "device_playback_ready_ms": None,
        "tts_first_pcm_ms": None,
        "gateway_first_packet_ms": None,
        "device_speaker_started_ms": 800,
    }


def test_app_lifespan_closes_realtime_provider_bundle(tmp_path) -> None:
    class _ClosableProviders:
        close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    app = create_app(
        Settings(
            app_env="test",
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'close.db').as_posix()}",
            admin_api_key="test-admin-key",
            jwt_secret="test-jwt-secret-with-enough-entropy",
            device_credential_pepper="test-device-pepper-with-enough-entropy",
            memory_master_key="test-memory-key-with-enough-entropy",
            provider_mode="mock",
        ),
        include_device_gateway=False,
    )
    providers = _ClosableProviders()
    app.state.realtime_providers = providers

    with TestClient(app):
        pass

    assert providers.close_calls == 1


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


class EmptyAsr(ImmediateAsr):
    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult("", "neutral")


class FillerAsr(ImmediateAsr):
    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult("嗯。", "neutral")


class SlowLlm:
    async def reply_stream(
        self,
        request: LlmRequest,
        *,
        tool_executor=None,
    ) -> AsyncIterator[str]:
        del request, tool_executor
        await asyncio.sleep(60)
        yield "不应到达"


class SlowProviders:
    mock = True
    llm = SlowLlm()

    async def open_asr(self) -> ImmediateAsr:
        return ImmediateAsr()

    async def open_tts(self, voice: str, speech_rate: float = 1.0):
        del voice, speech_rate
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

    async def open_tts(self, voice: str, speech_rate: float = 1.0) -> Tts:
        del voice, speech_rate
        return self.Tts()


class OpenFailingAsrProviders(FallbackExerciseProviders):
    async def open_asr(self) -> FailingAsr:
        raise ConnectionResetError("realtime ASR handshake reset")


class ReplayedAsr(ImmediateAsr):
    def __init__(self, received_frames: list[bytes]) -> None:
        self.received_frames = received_frames

    async def send_audio(self, frame: bytes) -> None:
        self.received_frames.append(frame)

    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult("直连重试成功", "neutral")


class RecoveringOpenAsrProviders(FallbackExerciseProviders):
    def __init__(self) -> None:
        self.open_calls = 0
        self.received_frames: list[bytes] = []

    async def open_asr(self) -> ImmediateAsr:
        self.open_calls += 1
        if self.open_calls == 1:
            raise ConnectionResetError("realtime ASR capacity limit")
        return ReplayedAsr(self.received_frames)


class EmptyTranscriptProviders(FallbackExerciseProviders):
    async def open_asr(self) -> EmptyAsr:
        return EmptyAsr()


class FillerTranscriptProviders(FallbackExerciseProviders):
    class ForbiddenLlm:
        async def reply_stream(self, *args, **kwargs) -> AsyncIterator[str]:
            del args, kwargs
            raise AssertionError("ASR filler must not reach the LLM")
            yield "unreachable"

    llm = ForbiddenLlm()

    async def open_asr(self) -> FillerAsr:
        return FillerAsr()


class TtsPrewarmProbeProviders(FallbackExerciseProviders):
    class Llm:
        def __init__(self, owner: "TtsPrewarmProbeProviders") -> None:
            self.owner = owner

        async def reply_stream(self, *args, **kwargs) -> AsyncIterator[str]:
            del args, kwargs
            if self.owner.allow_first_token is None:
                await asyncio.sleep(0.05)
            else:
                await asyncio.wait_for(
                    asyncio.to_thread(self.owner.allow_first_token.wait),
                    timeout=1.0,
                )
            self.owner.tts_open_before_first_token = self.owner.tts_open_started
            self.owner.first_token_emitted = True
            yield "这是一个用于验证并行建连的简短回答。"

    def __init__(self, *, gate_first_token: bool = False) -> None:
        self.tts_open_started = False
        self.tts_open_before_first_token = False
        self.first_token_emitted = False
        self.allow_first_token = threading.Event() if gate_first_token else None
        self.llm = self.Llm(self)

    async def open_asr(self) -> ImmediateAsr:
        return ImmediateAsr()

    async def open_tts(self, voice: str, speech_rate: float = 1.0) -> FallbackExerciseProviders.Tts:
        del voice, speech_rate
        self.tts_open_started = True
        await asyncio.sleep(0.02)
        return self.Tts()


class EmptyReplyAfterTtsPrewarmProviders(FallbackExerciseProviders):
    class Llm:
        async def reply_stream(self, *args, **kwargs) -> AsyncIterator[str]:
            del args, kwargs
            await asyncio.sleep(0.02)
            return
            yield "unreachable"

    llm = Llm()

    async def open_asr(self) -> ImmediateAsr:
        return ImmediateAsr()


class SlowReplyAfterTtsPrewarmProviders(FallbackExerciseProviders):
    llm = SlowLlm()

    async def open_asr(self) -> ImmediateAsr:
        return ImmediateAsr()


class EncoderStartFailProviders(FallbackExerciseProviders):
    mock = False


class EncoderPrewarmProbeProviders(FallbackExerciseProviders):
    mock = False

    class Llm:
        def __init__(self, owner: "EncoderPrewarmProbeProviders") -> None:
            self.owner = owner

        async def reply_stream(self, *args, **kwargs) -> AsyncIterator[str]:
            del args, kwargs
            await asyncio.sleep(0.02)
            self.owner.encoder_started_before_first_token = self.owner.encoder_started
            yield "编码器并行预热测试。"

    def __init__(self) -> None:
        self.encoder_started = False
        self.encoder_started_before_first_token = False
        self.llm = self.Llm(self)


class PlaybackReadyOverlapProviders(FallbackExerciseProviders):
    mock = False

    class Llm:
        async def reply_stream(self, *args, **kwargs) -> AsyncIterator[str]:
            del args, kwargs
            yield "播放器准备和语音合成应该并行。"

    class Tts(FallbackExerciseProviders.Tts):
        def __init__(self, owner: "PlaybackReadyOverlapProviders") -> None:
            self.owner = owner

        async def synthesize(self, text: str) -> AsyncIterator[bytes]:
            del text
            self.owner.synthesis_started.set()
            yield b"pcm"

    def __init__(self) -> None:
        self.llm = self.Llm()
        self.synthesis_started = threading.Event()
        self.packet_reader_started = False

    async def open_asr(self) -> ImmediateAsr:
        return ImmediateAsr()

    async def open_tts(self, voice: str, speech_rate: float = 1.0) -> Tts:
        del voice, speech_rate
        return self.Tts(self)


class FailingFallbackProviders:
    class Speech:
        async def transcribe(self, audio_frames: list[bytes]) -> str:
            del audio_frames
            raise TimeoutError("batch ASR unavailable")

    speech = Speech()


def test_playback_handshake_starts_before_llm_first_token(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    providers = TtsPrewarmProbeProviders(gate_first_token=True)
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-PLAYBACK-PREWARM")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})

        start = None
        while start is None:
            message = websocket.receive_json()
            if message.get("type") == "tts" and message.get("state") == "start":
                start = message

        assert providers.tts_open_started is True
        assert providers.first_token_emitted is False

        assert providers.allow_first_token is not None
        providers.allow_first_token.set()
        websocket.send_json({**start, "state": "ready"})
        while True:
            message = websocket.receive()
            if message.get("bytes") is not None:
                continue
            payload = json.loads(message["text"])
            if payload.get("type") == "tts" and payload.get("state") == "stop":
                websocket.send_json({**payload, "state": "drained"})
                break
        assert websocket.receive_json()["state"] == "completed"


def test_empty_llm_reply_stops_prewarmed_playback(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = EmptyReplyAfterTtsPrewarmProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-EMPTY-PREWARM")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})

        start = None
        while start is None:
            message = websocket.receive_json()
            if message.get("type") == "tts" and message.get("state") == "start":
                start = message
        websocket.send_json({**start, "state": "ready"})

        stop = websocket.receive_json()
        error = websocket.receive_json()
        assert stop == {**start, "state": "stop"}
        assert error["type"] == "error"
        assert error["code"] == "empty-reply"


def test_abort_stops_prewarmed_playback_before_resetting_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = SlowReplyAfterTtsPrewarmProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ABORT-PREWARM")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})

        start = None
        while start is None:
            message = websocket.receive_json()
            if message.get("type") == "tts" and message.get("state") == "start":
                start = message
        websocket.send_json({"type": "abort"})

        stop = websocket.receive_json()
        aborted = websocket.receive_json()
        interrupted = websocket.receive_json()
        assert stop == {**start, "state": "stop"}
        assert aborted["type"] == "system"
        assert aborted["state"] == "aborted"
        assert interrupted["type"] == "llm"
        assert interrupted["emotion"] == "interrupted"


def assert_error_does_not_start_silent_playback(websocket) -> None:
    websocket.send_json({"type": "ping", "sequence": 17})
    pong = websocket.receive_json()
    assert pong["type"] == "pong"
    assert pong["sequence"] == 17


def test_tts_provider_connection_is_prewarmed_while_llm_builds_first_sentence(
    client: TestClient, admin_headers: dict[str, str], caplog
) -> None:
    caplog.set_level("INFO")
    providers = TtsPrewarmProbeProviders()
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-TTS-PREWARM")
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

        start = None
        while start is None:
            message = websocket.receive_json()
            if message.get("type") == "tts" and message.get("state") == "start":
                start = message
        websocket.send_json({**start, "state": "ready"})
        while True:
            message = websocket.receive_json()
            if message.get("type") == "tts" and message.get("state") == "sentence_start":
                break
        websocket.receive_bytes()
        reply_emotion = websocket.receive_json()
        assert reply_emotion["type"] == "llm"
        assert reply_emotion["emotion"] == "neutral"
        websocket.send_json(
            {
                "type": "device_stage",
                "stage": "speaker_pcm_started",
                "turn_id": start["turn_id"],
                "reply_id": start["reply_id"],
            }
        )
        stop = websocket.receive_json()
        assert stop["state"] == "stop"
        websocket.send_json({**stop, "state": "drained"})
        assert websocket.receive_json()["state"] == "completed"

    assert providers.tts_open_before_first_token is True
    metric_messages = [
        record.getMessage()
        for record in caplog.records
        if "voice turn timeline" in record.getMessage()
    ]
    assert len(metric_messages) == 1
    assert {
        record.name
        for record in caplog.records
        if "voice turn timeline" in record.getMessage()
    } == {"uvicorn.error"}
    for field in (
        "asr_session_finished_ms=",
        "llm_first_token_ms=",
        "first_speakable_text_ms=",
        "tts_connected_ms=",
        "device_playback_ready_ms=",
        "tts_first_pcm_ms=",
        "gateway_first_packet_ms=",
        "device_speaker_started_ms=",
    ):
        assert field in metric_messages[0]
        assert f"{field}None" not in metric_messages[0]
    assert "用于验证并行建连" not in metric_messages[0]
    first_packet_messages = [
        record.getMessage()
        for record in caplog.records
        if "tts first packet sent" in record.getMessage()
    ]
    assert len(first_packet_messages) == 1
    assert "lease_generation=" in first_packet_messages[0]
    assert "用于验证并行建连" not in first_packet_messages[0]
    outcome_messages = [
        record.getMessage()
        for record in caplog.records
        if "voice turn outcome " in record.getMessage()
    ]
    assert len(outcome_messages) == 1
    outcome = json.loads(outcome_messages[0].split("voice turn outcome ", 1)[1])
    assert outcome["outcome"] == "completed"
    assert outcome["error_code"] is None
    assert outcome["device_speaker_started_ms"] is not None
    assert "transcript" not in outcome
    assert "reply" not in outcome


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


def test_encoder_start_failure_stops_owned_reply_without_starting_a_second_reply(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = EncoderStartFailProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ENCODER-FAIL")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    original_ffmpeg_path = client.app.state.settings.ffmpeg_path
    client.app.state.settings.ffmpeg_path = "missing-hensun-ffmpeg"
    try:
        with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
            websocket.send_json(
                {
                    "type": "hello",
                    "version": 1,
                    "features": {"strict_playback_ack": True},
                }
            )
            websocket.receive_json()
            websocket.send_json({"type": "listen", "state": "start"})
            websocket.send_bytes(b"audio")
            websocket.send_json({"type": "listen", "state": "stop"})

            def receive_until(expected) -> dict[str, object]:
                while True:
                    message = websocket.receive_json()
                    if expected(message):
                        return message

            assert receive_until(lambda item: item.get("type") == "stt")["type"] == "stt"
            assert receive_until(
                lambda item: item.get("type") == "llm" and item.get("emotion") == "thinking"
            )["emotion"] == "thinking"
            start = receive_until(
                lambda item: item.get("type") == "tts" and item.get("state") == "start"
            )
            assert start["state"] == "start"
            websocket.send_json({**start, "state": "ready"})

            messages_before_error: list[dict[str, object]] = []
            while True:
                error = websocket.receive_json()
                if error.get("type") == "error":
                    break
                messages_before_error.append(error)
            stop = websocket.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "ai-unavailable"
            assert not any(
                message.get("type") == "tts" and message.get("state") == "start"
                for message in messages_before_error
            )
            assert stop == {**start, "state": "stop"}
    finally:
        client.app.state.settings.ffmpeg_path = original_ffmpeg_path


def test_streaming_encoder_is_prewarmed_while_llm_builds_first_sentence(
    client: TestClient, admin_headers: dict[str, str], monkeypatch
) -> None:
    providers = EncoderPrewarmProbeProviders()

    class _FakeEncoder:
        def __init__(self, ffmpeg_path: str) -> None:
            del ffmpeg_path
            self.packets_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            self.finished = False

        async def start(self) -> None:
            providers.encoder_started = True

        async def write(self, pcm: bytes) -> None:
            await self.packets_queue.put(pcm)

        async def packets(self, *, prebuffer_packets: int = 0):
            del prebuffer_packets
            while True:
                packet = await self.packets_queue.get()
                if packet is None:
                    return
                yield packet

        async def finish(self) -> None:
            if not self.finished:
                self.finished = True
                await self.packets_queue.put(None)

        async def cancel(self) -> None:
            await self.finish()

    monkeypatch.setattr(realtime_session, "StreamingPcmToOpus", _FakeEncoder)
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ENCODER-PREWARM")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})
        while True:
            message = websocket.receive()
            if message.get("bytes") is not None:
                continue
            payload = json.loads(message["text"])
            if payload.get("type") == "tts" and payload.get("state") == "stop":
                websocket.send_json({**payload, "state": "drained"})
                break
        assert websocket.receive_json()["state"] == "completed"

    assert providers.encoder_started_before_first_token is True


def test_tts_synthesis_overlaps_strict_device_ready_without_sending_audio_early(
    client: TestClient, admin_headers: dict[str, str], monkeypatch
) -> None:
    providers = PlaybackReadyOverlapProviders()

    class _FakeEncoder:
        def __init__(self, ffmpeg_path: str) -> None:
            del ffmpeg_path
            self.packets_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def start(self) -> None:
            return None

        async def write(self, pcm: bytes) -> None:
            await self.packets_queue.put(pcm)

        async def packets(self, *, prebuffer_packets: int = 0):
            del prebuffer_packets
            providers.packet_reader_started = True
            while True:
                packet = await self.packets_queue.get()
                if packet is None:
                    return
                yield packet

        async def finish(self) -> None:
            await self.packets_queue.put(None)

        async def cancel(self) -> None:
            await self.finish()

    monkeypatch.setattr(realtime_session, "StreamingPcmToOpus", _FakeEncoder)
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-READY-OVERLAP")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "version": 1,
                "features": {"strict_playback_ack": True},
            }
        )
        websocket.receive_json()
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})

        start = None
        messages_before_start: list[dict[str, object]] = []
        while start is None:
            message = websocket.receive_json()
            if message.get("type") == "tts" and message.get("state") == "start":
                start = message
            else:
                messages_before_start.append(message)

        assert [
            message.get("emotion")
            for message in messages_before_start
            if message.get("type") == "llm"
        ] == ["thinking"]

        assert providers.synthesis_started.wait(timeout=1.0)
        assert providers.packet_reader_started is False

        websocket.send_json({**start, "state": "ready"})
        messages_after_start: list[dict[str, object]] = []
        while True:
            message = websocket.receive_json()
            messages_after_start.append(message)
            if message.get("type") == "tts" and message.get("state") == "sentence_start":
                break
        assert not any(message.get("type") == "llm" for message in messages_after_start)
        assert websocket.receive_bytes()
        reply_emotion = websocket.receive_json()
        assert reply_emotion["type"] == "llm"
        assert reply_emotion["emotion"] == "neutral"
        websocket.send_json(
            {
                "type": "device_stage",
                "stage": "speaker_pcm_started",
                "turn_id": start["turn_id"],
                "reply_id": start["reply_id"],
            }
        )
        stop = websocket.receive_json()
        assert stop["state"] == "stop"
        websocket.send_json({**stop, "state": "drained"})
        assert websocket.receive_json()["state"] == "completed"


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
        start = websocket.receive_json()
        assert start["state"] == "start"
        websocket.send_json(
            {
                "type": "tts",
                "state": "ready",
                "turn_id": start["turn_id"],
                "reply_id": start["reply_id"],
            }
        )
        assert websocket.receive_json()["state"] == "sentence_start"
        websocket.receive_bytes()
        assert websocket.receive_json()["type"] == "llm"
        stop = websocket.receive_json()
        assert stop["state"] == "stop"
        websocket.send_json(
            {
                "type": "tts",
                "state": "drained",
                "turn_id": stop["turn_id"],
                "reply_id": stop["reply_id"],
            }
        )
        completed = websocket.receive_json()
        assert completed["type"] == "turn"
        assert completed["state"] == "completed"
        assert completed["turn_id"] == stop["turn_id"]

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


def test_realtime_asr_open_failure_keeps_device_connected_and_uses_batch_fallback(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = OpenFailingAsrProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ASR-OPEN-FAILED")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("建连失败备用识别".encode())
        websocket.send_json({"type": "listen", "state": "stop"})

        stt = websocket.receive_json()
        assert stt["type"] == "stt"
        assert stt["text"] == "建连失败备用识别"


def test_realtime_asr_open_failure_replays_buffered_audio_once_before_failing_turn(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    providers = RecoveringOpenAsrProviders()
    client.app.state.realtime_providers = providers
    client.app.state.fallback_providers = None
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ASR-OPEN-RETRY")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"buffered-audio")
        websocket.send_json({"type": "listen", "state": "stop"})

        stt = websocket.receive_json()
        assert stt["type"] == "stt"
        assert stt["text"] == "直连重试成功"

    assert providers.open_calls == 2
    assert providers.received_frames == [b"buffered-audio"]


def test_empty_transcript_reopens_listening_without_error_face(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = EmptyTranscriptProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-NO-SPEECH")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"silence")
        websocket.send_json({"type": "listen", "state": "stop"})
        resume = websocket.receive_json()

    assert resume["type"] == "listen"
    assert resume["state"] == "resume"


def test_asr_filler_silently_returns_device_to_followup_listening(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = FillerTranscriptProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ASR-FILLER")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"room-noise")
        websocket.send_json({"type": "listen", "state": "stop"})

        resume = websocket.receive_json()
        assert resume["type"] == "listen"
        assert resume["state"] == "resume"
        assert resume["turn_id"]


def test_consecutive_asr_fillers_keep_followup_listening_without_fake_tts(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = FillerTranscriptProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ASR-FILLER-BOUND")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        for attempt in range(3):
            websocket.send_json({"type": "listen", "state": "start"})
            websocket.send_bytes(f"room-noise-{attempt}".encode())
            websocket.send_json({"type": "listen", "state": "stop"})
            response = websocket.receive_json()
            assert response["type"] == "listen"
            assert response["state"] == "resume"


def test_realtime_and_batch_asr_failure_returns_stable_error(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = FallbackExerciseProviders()
    client.app.state.fallback_providers = FailingFallbackProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ASR-FAILED")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})
        error = websocket.receive_json()
        assert_error_does_not_start_silent_playback(websocket)

    assert error["type"] == "error"
    assert error["code"] == "asr-fallback-failed"


def test_realtime_asr_failure_without_fallback_returns_stable_error(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = FallbackExerciseProviders()
    client.app.state.fallback_providers = None
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-ASR-REALTIME-FAILED")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})
        error = websocket.receive_json()
        assert_error_does_not_start_silent_playback(websocket)

    assert error["type"] == "error"
    assert error["code"] == "asr-realtime-invalid"


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


def test_late_listen_start_keeps_audio_already_buffered(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-LATE-START")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_bytes("前半".encode())
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("后半".encode())
        websocket.send_json({"type": "listen", "state": "stop"})
        stt = websocket.receive_json()
        assert stt["type"] == "stt"
        assert stt["text"] == "前半后半"


def test_device_audio_buffer_has_a_configured_frame_limit(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert client.app.state.settings.max_device_audio_queue_frames * 60 >= 60_000
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-AUDIO-LIMIT")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }
    original_limit = client.app.state.settings.max_device_audio_queue_frames
    client.app.state.settings.max_device_audio_queue_frames = 3
    try:
        with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
            websocket.send_json({"type": "listen", "state": "start"})
            for _ in range(4):
                websocket.send_bytes(b"frame")
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "audio-frame-limit"
    finally:
        client.app.state.settings.max_device_audio_queue_frames = original_limit


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
        started = websocket.receive_json()
        assert started["type"] == "tts"
        assert started["state"] == "start"
        sentence = websocket.receive_json()
        assert sentence["state"] == "sentence_start"
        assert "休息时段" in sentence["text"]
        assert websocket.receive_bytes()
        emotion = websocket.receive_json()
        assert emotion["type"] == "llm"
        assert emotion["emotion"] == "safe_block"
        stopped = websocket.receive_json()
        assert stopped["type"] == "tts"
        assert stopped["state"] == "stop"
