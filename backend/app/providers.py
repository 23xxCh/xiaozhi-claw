import base64
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .audio_formats import FfmpegOpusNormalizer, opus_packets_to_ogg
from .config import Settings

SYSTEM_PROMPT = """你是 Hensun Desk，一台面向成年人的桌面 AI 助理。
你必须明确自己是 AI，不冒充真人，不声称拥有真实情感或身体。
你可以提供轻度陪伴，但不能诱导排他关系、依赖、消费或替代现实社交。
回答尽量口语化，通常不超过 50 个汉字；医疗、法律、金融问题提示用户咨询专业人士。
用户要求停止或退出时立即停止互动。"""


class SpeechProvider(Protocol):
    async def transcribe(self, audio_frames: list[bytes]) -> str: ...

    async def synthesize(self, text: str) -> list[bytes]: ...


class LlmProvider(Protocol):
    async def reply(self, text: str, memories: list[str]) -> str: ...


class TtsAudioNormalizer(Protocol):
    async def normalize_tts(self, audio: bytes) -> list[bytes]: ...


class MockSpeechProvider:
    async def transcribe(self, audio_frames: list[bytes]) -> str:
        try:
            return b"".join(audio_frames).decode("utf-8").strip()
        except UnicodeDecodeError:
            return "测试语音"

    async def synthesize(self, text: str) -> list[bytes]:
        return [text.encode("utf-8")]


class MockLlmProvider:
    async def reply(self, text: str, memories: list[str]) -> str:
        prefix = "我记得你的偏好。" if memories else ""
        return f"{prefix}收到：{text}"[:50]


class OpenAICompatibleSpeechProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        normalizer: TtsAudioNormalizer | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.normalizer = normalizer or FfmpegOpusNormalizer(settings.ffmpeg_path)

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.client is not None:
            return await self.client.post(url, **kwargs)
        async with httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds) as client:
            return await client.post(url, **kwargs)

    async def transcribe(self, audio_frames: list[bytes]) -> str:
        ogg_audio = opus_packets_to_ogg(
            audio_frames,
            input_sample_rate=16000,
            frame_duration_ms=60,
        )
        response = await self._post(
            self.settings.asr_url,
            headers={"Authorization": f"Bearer {self.settings.asr_api_key}"},
            data={"model": self.settings.asr_model},
            files={"file": ("speech.ogg", ogg_audio, "audio/ogg")},
        )
        response.raise_for_status()
        text = response.json().get("text", "")
        return str(text).strip()

    async def synthesize(self, text: str) -> list[bytes]:
        response = await self._post(
            self.settings.tts_url,
            headers={"Authorization": f"Bearer {self.settings.tts_api_key}"},
            json={
                "model": self.settings.tts_model,
                "voice": self.settings.tts_voice,
                "input": text,
                "response_format": self.settings.tts_response_format,
            },
        )
        response.raise_for_status()
        return await self.normalizer.normalize_tts(response.content)


def _append_path(url: str, path: str) -> str:
    """Accept either a full endpoint or a provider base URL."""
    normalized = url.rstrip("/")
    if normalized.endswith(path):
        return normalized
    return f"{normalized}{path}"


def _message_text(message: object) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "".join(
            str(item.get("text", ""))
            for item in message
            if isinstance(item, dict) and item.get("type") in {None, "text"}
        )
    return ""


def _message_emotion(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    annotations = message.get("annotations")
    if not isinstance(annotations, list):
        return None
    for annotation in annotations:
        if isinstance(annotation, dict) and annotation.get("emotion"):
            return str(annotation["emotion"])
    return None


class QwenDashScopeSpeechProvider:
    """Batch Qwen ASR via OpenAI compatibility and TTS via DashScope native API."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        normalizer: TtsAudioNormalizer | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.normalizer = normalizer or FfmpegOpusNormalizer(settings.ffmpeg_path)
        self.last_emotion: str | None = None

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.client is not None:
            return await self.client.post(url, **kwargs)
        async with httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds) as client:
            return await client.post(url, **kwargs)

    async def _get(self, url: str) -> httpx.Response:
        if self.client is not None:
            return await self.client.get(url)
        async with httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds) as client:
            return await client.get(url)

    async def transcribe(self, audio_frames: list[bytes]) -> str:
        ogg_audio = opus_packets_to_ogg(
            audio_frames,
            input_sample_rate=16000,
            frame_duration_ms=60,
        )
        data_uri = "data:audio/ogg;base64," + base64.b64encode(ogg_audio).decode("ascii")
        response = await self._post(
            _append_path(self.settings.asr_url, "/chat/completions"),
            headers={"Authorization": f"Bearer {self.settings.asr_api_key}"},
            json={
                "model": self.settings.asr_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": data_uri, "format": "ogg"},
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {"language": "zh", "enable_itn": True},
            },
        )
        response.raise_for_status()
        payload = response.json()
        message = payload["choices"][0]["message"]
        self.last_emotion = _message_emotion(message)
        content = message.get("content") if isinstance(message, dict) else ""
        return _message_text(content).strip()

    async def synthesize(self, text: str) -> list[bytes]:
        response = await self._post(
            self.settings.tts_url,
            headers={"Authorization": f"Bearer {self.settings.tts_api_key}"},
            json={
                "model": self.settings.tts_model,
                "input": {
                    "text": text,
                    "voice": self.settings.tts_voice,
                    "language_type": self.settings.tts_language_type,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        audio = payload.get("output", {}).get("audio", {})
        audio_data = str(audio.get("data") or "")
        if audio_data:
            raw_audio = base64.b64decode(audio_data)
        else:
            audio_url = str(audio.get("url") or "")
            if not audio_url:
                raise RuntimeError("DashScope TTS response did not contain audio data or URL")
            audio_response = await self._get(audio_url)
            audio_response.raise_for_status()
            raw_audio = audio_response.content
        return await self.normalizer.normalize_tts(raw_audio)


class OpenAICompatibleLlmProvider:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    async def _post(self, **kwargs: Any) -> httpx.Response:
        if self.client is not None:
            return await self.client.post(
                _append_path(self.settings.llm_url, "/chat/completions"), **kwargs
            )
        async with httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds) as client:
            return await client.post(
                _append_path(self.settings.llm_url, "/chat/completions"), **kwargs
            )

    async def reply(self, text: str, memories: list[str]) -> str:
        memory_block = "\n".join(f"- {item}" for item in memories[:10])
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if memory_block:
            messages.append(
                {
                    "role": "system",
                    "content": f"用户主动授权保存的摘要记忆：\n{memory_block}",
                }
            )
        messages.append({"role": "user", "content": text})
        response = await self._post(
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            json={
                "model": self.settings.llm_model,
                "messages": messages,
                "max_tokens": 150,
                "temperature": 0.6,
                "stream": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["choices"][0]["message"]["content"]).strip()


@dataclass(slots=True)
class ProviderBundle:
    speech: SpeechProvider
    llm: LlmProvider
    audio_codec: str


def create_providers(settings: Settings) -> ProviderBundle:
    if settings.provider_mode == "mock":
        return ProviderBundle(MockSpeechProvider(), MockLlmProvider(), "mock-utf8")
    uses_dashscope_speech = (
        settings.asr_protocol == "qwen-chat-completions"
        or settings.tts_protocol == "dashscope-generation"
    )
    if uses_dashscope_speech:
        speech: SpeechProvider = QwenDashScopeSpeechProvider(settings)
    else:
        speech = OpenAICompatibleSpeechProvider(settings)
    return ProviderBundle(
        speech,
        OpenAICompatibleLlmProvider(settings),
        "opus",
    )


def create_fallback_providers(settings: Settings) -> ProviderBundle | None:
    """Build the bounded batch fallback without exposing provider routes to clients."""
    if not settings.fallback_enabled:
        return None
    if settings.provider_mode == "mock":
        return create_providers(settings)
    api_key = settings.fallback_api_key or settings.asr_api_key
    if not api_key:
        return None
    fallback = settings.model_copy(
        update={
            "provider_mode": "custom",
            "asr_protocol": "qwen-chat-completions",
            "asr_url": settings.fallback_asr_url,
            "asr_api_key": api_key,
            "asr_model": settings.fallback_asr_model,
            "llm_url": settings.fallback_llm_url,
            "llm_api_key": api_key,
            "llm_model": settings.fallback_llm_model,
            "tts_protocol": "dashscope-generation",
            "tts_url": settings.fallback_tts_url,
            "tts_api_key": api_key,
            "tts_model": settings.fallback_tts_model,
            "tts_voice": settings.fallback_tts_voice,
        }
    )
    return create_providers(fallback)
