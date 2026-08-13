import json

import httpx
import pytest

from backend.app.config import Settings
from backend.realtime import providers as realtime_providers
from backend.realtime.providers import DeepSeekStreamingLlmProvider, QwenRealtimeTtsSession


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
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        return json.dumps({"type": "session.updated"})

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
