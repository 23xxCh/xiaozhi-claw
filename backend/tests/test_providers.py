import json

import httpx
import pytest

from backend.app.config import Settings
from backend.app.providers import (
    OpenAICompatibleLlmProvider,
    OpenAICompatibleSpeechProvider,
    create_providers,
)


class StubNormalizer:
    def __init__(self) -> None:
        self.received = b""

    async def normalize_tts(self, audio: bytes) -> list[bytes]:
        self.received = audio
        return [b"normalized-opus-1", b"normalized-opus-2"]


def custom_settings() -> Settings:
    return Settings(
        provider_mode="custom",
        asr_url="https://speech.example/v1/audio/transcriptions",
        asr_api_key="asr-secret",
        asr_model="asr-model",
        tts_url="https://speech.example/v1/audio/speech",
        tts_api_key="tts-secret",
        tts_model="tts-model",
        tts_voice="voice-1",
        llm_url="https://llm.example/v1/chat/completions",
        llm_api_key="llm-secret",
        llm_model="llm-model",
    )


@pytest.mark.asyncio
async def test_custom_speech_provider_calls_asr_and_normalizes_tts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("transcriptions"):
            return httpx.Response(200, json={"text": "你好"})
        return httpx.Response(200, content=b"vendor-mp3")

    normalizer = StubNormalizer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleSpeechProvider(
            custom_settings(), client=client, normalizer=normalizer
        )
        transcript = await provider.transcribe([b"opus-one", b"opus-two"])
        audio = await provider.synthesize("你好，我是 Hensun")

    assert transcript == "你好"
    assert normalizer.received == b"vendor-mp3"
    assert audio == [b"normalized-opus-1", b"normalized-opus-2"]
    assert requests[0].headers["authorization"] == "Bearer asr-secret"
    assert b"speech.ogg" in requests[0].content
    assert requests[1].headers["authorization"] == "Bearer tts-secret"
    assert json.loads(requests[1].content)["voice"] == "voice-1"


@pytest.mark.asyncio
async def test_custom_llm_provider_uses_configured_model_and_memory() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "今天记得喝水"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleLlmProvider(custom_settings(), client=client)
        reply = await provider.reply("提醒我", ["用户喜欢温水"])

    assert reply == "今天记得喝水"
    assert captured["model"] == "llm-model"
    assert any("用户喜欢温水" in item["content"] for item in captured["messages"])


def test_custom_provider_bundle_announces_opus() -> None:
    providers = create_providers(custom_settings())
    assert providers.audio_codec == "opus"
