from dataclasses import dataclass
from typing import Protocol

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

    async def _post(self, url: str, **kwargs: object) -> httpx.Response:
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


class OpenAICompatibleLlmProvider:
    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client

    async def _post(self, **kwargs: object) -> httpx.Response:
        if self.client is not None:
            return await self.client.post(self.settings.llm_url, **kwargs)
        async with httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds) as client:
            return await client.post(self.settings.llm_url, **kwargs)

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
    return ProviderBundle(
        OpenAICompatibleSpeechProvider(settings),
        OpenAICompatibleLlmProvider(settings),
        "opus",
    )
