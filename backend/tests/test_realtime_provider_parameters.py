import asyncio
import base64
import json

import httpx
import pytest

from backend.app.audio_formats import ogg_opus_packets
from backend.app.config import Settings
from backend.realtime import providers as realtime_providers
from backend.realtime.providers import (
    DeepSeekStreamingLlmProvider,
    QwenRealtimeAsrSession,
    QwenRealtimeTtsSession,
    RealtimeProviderError,
)
from backend.realtime.session import SentenceBuffer, sanitize_spoken_text


@pytest.mark.asyncio
async def test_deepseek_streaming_request_includes_selected_temperature(monkeypatch) -> None:
    captured: dict[str, object] = {}
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n',
        )

    def client_factory(*args, **kwargs) -> httpx.AsyncClient:
        del args, kwargs
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(realtime_providers.httpx, "AsyncClient", client_factory)
    settings = Settings(
        provider_mode="custom",
        llm_url="https://llm.example/v1",
        llm_api_key="secret",
        llm_model="deepseek-v4-flash",
    )
    provider = DeepSeekStreamingLlmProvider(settings)

    chunks = [
        chunk
        async for chunk in provider.reply_stream(
            "你好",
            [],
            [],
            system_prompt="你是助手",
            model="deepseek-v4-flash",
            temperature=0.35,
        )
    ]

    assert chunks == ["好"]
    assert captured["temperature"] == 0.35


class _FakeRealtimeSocket:
    def __init__(self, events: list[dict[str, object]] | None = None) -> None:
        self.sent: list[str] = []
        self.events = list(events or [{"type": "session.updated"}])

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        return json.dumps(self.events.pop(0))

    async def close(self) -> None:
        return None


class _PlaybackSensitiveRealtimeSocket(_FakeRealtimeSocket):
    """Simulate a provider whose final event must be drained promptly."""

    def __init__(self) -> None:
        super().__init__(
            [
                {"type": "response.audio.delta", "delta": base64.b64encode(b"pcm").decode()},
                {"type": "response.done"},
            ]
        )
        self.first_audio_read_at: float | None = None

    async def recv(self) -> str:
        if self.first_audio_read_at is not None:
            if asyncio.get_running_loop().time() - self.first_audio_read_at > 0.01:
                raise TimeoutError("provider final event was not drained while playback was slow")
        payload = await super().recv()
        if self.first_audio_read_at is None:
            self.first_audio_read_at = asyncio.get_running_loop().time()
        return payload


@pytest.mark.asyncio
async def test_qwen_realtime_tts_session_includes_selected_speech_rate(monkeypatch) -> None:
    socket = _FakeRealtimeSocket()

    async def fake_connect(*args, **kwargs):
        del args, kwargs
        return socket

    monkeypatch.setattr(realtime_providers, "connect", fake_connect)
    settings = Settings(
        provider_mode="custom",
        qwen_realtime_tts_url="wss://tts.example/realtime",
        qwen_realtime_tts_model="qwen3-tts-flash-realtime",
        tts_api_key="secret",
    )

    await QwenRealtimeTtsSession.open(settings, voice="Cherry", speech_rate=1.2)

    update = json.loads(socket.sent[0])
    assert update["type"] == "session.update"
    assert update["session"]["voice"] == "Cherry"
    assert update["session"]["speech_rate"] == 1.2


@pytest.mark.asyncio
async def test_qwen_tts_provider_timeout_does_not_count_slow_audio_consumer() -> None:
    socket = _FakeRealtimeSocket(
        [
            {"type": "response.audio.delta", "delta": base64.b64encode(b"pcm").decode()},
            {"type": "response.done"},
        ]
    )
    session = QwenRealtimeTtsSession(socket, event_timeout_seconds=0.01)

    chunks: list[bytes] = []
    async for chunk in session.synthesize("较长回复"):
        chunks.append(chunk)
        await asyncio.sleep(0.02)

    assert chunks == [b"pcm"]


@pytest.mark.asyncio
async def test_qwen_tts_drains_provider_while_playback_consumer_is_slow() -> None:
    socket = _PlaybackSensitiveRealtimeSocket()
    session = QwenRealtimeTtsSession(socket, event_timeout_seconds=0.1)

    chunks: list[bytes] = []
    async for chunk in session.synthesize("较长回复"):
        chunks.append(chunk)
        await asyncio.sleep(0.02)

    assert chunks == [b"pcm"]


def test_sentence_buffer_prefers_natural_clause_over_mid_sentence_split() -> None:
    buffer = SentenceBuffer()

    assert buffer.feed("很抱歉，我无法直接获取实时时间，建议您查看") == [
        "很抱歉，我无法直接获取实时时间，"
    ]
    assert buffer.flush() == "建议您查看"


def test_sentence_buffer_starts_unpunctuated_reply_without_waiting_for_full_sentence() -> None:
    buffer = SentenceBuffer()

    assert buffer.feed("短" * 15) == []
    assert buffer.feed("句") == ["短" * 15 + "句"]
    assert buffer.feed("后续内容") == []
    assert buffer.flush() == "后续内容"


def test_sentence_buffer_keeps_hard_limit_after_first_chunk() -> None:
    buffer = SentenceBuffer()

    assert buffer.feed("短" * 16) == ["短" * 16]
    assert buffer.feed("句" * 32) == ["句" * 32]


def test_sanitize_spoken_text_removes_non_speech_markup() -> None:
    assert (
        sanitize_spoken_text(
            "[[face:happy]]**好的**，[mood:happy]（轻轻点头）请看 https://example.com/path"
        )
        == "好的，请看"
    )


@pytest.mark.asyncio
async def test_qwen_realtime_asr_wraps_raw_opus_and_uses_server_vad(
    monkeypatch,
) -> None:
    socket = _FakeRealtimeSocket(
        [
            {"type": "session.updated"},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "你好，小智",
                "emotion": "happy",
            },
            {"type": "session.finished"},
        ]
    )

    async def fake_connect(*args, **kwargs):
        del args, kwargs
        return socket

    monkeypatch.setattr(realtime_providers, "connect", fake_connect)
    settings = Settings(
        provider_mode="custom",
        qwen_realtime_asr_url="wss://asr.example/realtime",
        qwen_realtime_asr_model="qwen3-asr-flash-realtime",
        qwen_realtime_vad_silence_ms=700,
        asr_api_key="secret",
    )

    session = await QwenRealtimeAsrSession.open(settings)
    await session.send_audio(b"raw-opus-one")
    await session.send_audio(b"raw-opus-two")
    for _ in range(10):
        if session.endpoint_detected():
            break
        await asyncio.sleep(0)
    result = await session.finish()

    messages = [json.loads(payload) for payload in socket.sent]
    assert messages[0]["session"]["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.5,
        "silence_duration_ms": 700,
    }
    append_messages = [item for item in messages if item["type"] == "input_audio_buffer.append"]
    wrapped = b"".join(base64.b64decode(item["audio"]) for item in append_messages)
    assert ogg_opus_packets(wrapped) == [b"raw-opus-one", b"raw-opus-two"]
    assert session.endpoint_detected() is True
    assert messages[-1]["type"] == "session.finish"
    assert not any(item["type"] == "input_audio_buffer.commit" for item in messages)
    assert result.text == "你好，小智"
    assert result.emotion == "happy"


@pytest.mark.asyncio
async def test_qwen_realtime_error_uses_stable_code_without_transcript(monkeypatch) -> None:
    socket = _FakeRealtimeSocket(
        [
            {"type": "session.updated"},
            {
                "type": "invalid_request_error",
                "error": {"type": "invalid_audio", "message": "private audio detail"},
            },
        ]
    )

    async def fake_connect(*args, **kwargs):
        del args, kwargs
        return socket

    monkeypatch.setattr(realtime_providers, "connect", fake_connect)
    settings = Settings(
        provider_mode="custom",
        qwen_realtime_asr_url="wss://asr.example/realtime",
        asr_api_key="secret",
    )
    session = await QwenRealtimeAsrSession.open(settings)
    await session.send_audio(b"raw-opus")

    with pytest.raises(RealtimeProviderError, match="invalid_audio") as error:
        await session.finish()
    assert "private audio detail" not in str(error.value)


@pytest.mark.asyncio
async def test_qwen_local_stop_commits_before_finishing_server_vad() -> None:
    socket = _FakeRealtimeSocket(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "本地提前停止",
            },
            {"type": "session.finished"},
        ]
    )
    session = QwenRealtimeAsrSession(socket)
    session._reader_task = asyncio.create_task(session._read_events())

    result = await session.finish()

    messages = [json.loads(payload) for payload in socket.sent]
    assert [item["type"] for item in messages] == [
        "input_audio_buffer.commit",
        "session.finish",
    ]
    assert result.text == "本地提前停止"
