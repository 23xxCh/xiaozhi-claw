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
from backend.realtime.session import SentenceBuffer


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


def test_sentence_buffer_releases_unpunctuated_first_audio_promptly() -> None:
    buffer = SentenceBuffer()

    assert buffer.feed("短" * 35) == []
    assert buffer.feed("句") == ["短" * 35 + "句"]


@pytest.mark.asyncio
async def test_qwen_realtime_asr_wraps_raw_opus_and_finishes_manual_session(
    monkeypatch,
) -> None:
    socket = _FakeRealtimeSocket(
        [
            {"type": "session.updated"},
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
        asr_api_key="secret",
    )

    session = await QwenRealtimeAsrSession.open(settings)
    await session.send_audio(b"raw-opus-one")
    await session.send_audio(b"raw-opus-two")
    result = await session.finish()

    messages = [json.loads(payload) for payload in socket.sent]
    append_messages = [item for item in messages if item["type"] == "input_audio_buffer.append"]
    wrapped = b"".join(base64.b64decode(item["audio"]) for item in append_messages)
    assert ogg_opus_packets(wrapped) == [b"raw-opus-one", b"raw-opus-two"]
    assert [item["type"] for item in messages[-2:]] == [
        "input_audio_buffer.commit",
        "session.finish",
    ]
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
