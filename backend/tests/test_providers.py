import json

import httpx
import pytest

from backend.ai.context import LlmContext, LlmRequest
from backend.app.config import Settings
from backend.app.providers import (
    OpenAICompatibleLlmProvider,
    OpenAICompatibleSpeechProvider,
    QwenDashScopeSpeechProvider,
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
        reply = await provider.reply(
            LlmRequest(
                context=LlmContext(
                    messages=(
                        {"role": "system", "content": "你是助手"},
                        {"role": "system", "content": "用户喜欢温水"},
                        {"role": "user", "content": "提醒我"},
                    ),
                    estimated_input_tokens=12,
                    source_token_counts={},
                    selected_memory_ids=("memory-1",),
                ),
                model="llm-model",
                temperature=0.6,
            )
        )

    assert reply == "今天记得喝水"
    assert captured["model"] == "llm-model"
    assert any("用户喜欢温水" in item["content"] for item in captured["messages"])


@pytest.mark.asyncio
async def test_batch_llm_fallback_preserves_voice_prompt_and_history() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "周五"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleLlmProvider(custom_settings(), client=client)
        reply = await provider.reply(
            LlmRequest(
                context=LlmContext(
                    messages=(
                        {
                            "role": "system",
                            "content": "可信本地日期：2026年8月28日，星期五。",
                        },
                        {"role": "user", "content": "我们刚才在聊周末安排"},
                        {"role": "assistant", "content": "记得你想去散步"},
                        {"role": "user", "content": "今天星期几"},
                    ),
                    estimated_input_tokens=24,
                    source_token_counts={},
                ),
                model="llm-model",
                temperature=0.6,
            )
        )

    assert reply == "周五"
    messages = captured["messages"]
    assert messages[0] == {
        "role": "system",
        "content": "可信本地日期：2026年8月28日，星期五。",
    }
    assert messages[-3:] == [
        {"role": "user", "content": "我们刚才在聊周末安排"},
        {"role": "assistant", "content": "记得你想去散步"},
        {"role": "user", "content": "今天星期几"},
    ]


def test_custom_provider_bundle_announces_opus() -> None:
    providers = create_providers(custom_settings())
    assert providers.audio_codec == "opus"


@pytest.mark.asyncio
async def test_qwen_speech_provider_uses_compatible_asr_and_dashscope_tts() -> None:
    settings = custom_settings()
    settings.asr_protocol = "qwen-chat-completions"
    settings.asr_url = "https://dashscope.example/compatible-mode/v1"
    settings.asr_model = "qwen3-asr-flash"
    settings.tts_protocol = "dashscope-generation"
    settings.tts_url = (
        "https://dashscope.example/api/v1/services/aigc/multimodal-generation/generation"
    )
    settings.tts_model = "qwen3-tts-flash"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("chat/completions"):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "你好",
                                "annotations": [{"emotion": "happy"}],
                            }
                        }
                    ]
                },
            )
        if request.url.path.endswith("generation"):
            return httpx.Response(
                200,
                json={"output": {"audio": {"url": "https://audio.example/result.wav"}}},
            )
        return httpx.Response(200, content=b"vendor-wav")

    normalizer = StubNormalizer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QwenDashScopeSpeechProvider(settings, client=client, normalizer=normalizer)
        transcript = await provider.transcribe([b"opus-one"])
        audio = await provider.synthesize("你好")

    assert transcript == "你好"
    assert provider.last_emotion == "happy"
    assert audio == [b"normalized-opus-1", b"normalized-opus-2"]
    assert requests[0].url.path.endswith("/compatible-mode/v1/chat/completions")
    asr_payload = json.loads(requests[0].content)
    assert asr_payload["model"] == "qwen3-asr-flash"
    assert asr_payload["messages"][0]["content"][0]["type"] == "input_audio"
    assert requests[1].url.path.endswith("/generation")
