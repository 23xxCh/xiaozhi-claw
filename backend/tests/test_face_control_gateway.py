import json
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from backend.realtime.providers import TranscriptionResult

from .conftest import provision_owned_device


class ImmediateAsr:
    async def send_audio(self, frame: bytes) -> None:
        del frame

    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult("测试表情", "neutral")

    async def cancel(self) -> None:
        return None


class FaceTaggedProviders:
    mock = True

    def __init__(self) -> None:
        self.spoken_texts: list[str] = []

        class Llm:
            async def reply_stream(inner_self, *args, **kwargs) -> AsyncIterator[str]:
                del inner_self, args, kwargs
                for chunk in (
                    "[[fa",
                    "ce:happy]]今天会很顺利。",
                    "[[face:curious]]还想聊点什么？",
                ):
                    yield chunk

        class Tts:
            async def synthesize(inner_self, text: str) -> AsyncIterator[bytes]:
                del inner_self
                self.spoken_texts.append(text)
                yield text.encode()

            async def finish(inner_self) -> None:
                del inner_self

            async def cancel(inner_self) -> None:
                del inner_self

        self.llm = Llm()
        self._tts_type = Tts

    async def open_asr(self) -> ImmediateAsr:
        return ImmediateAsr()

    async def open_tts(self, voice: str, speech_rate: float = 1.0):
        del voice, speech_rate
        return self._tts_type()


def test_streamed_face_controls_change_expression_without_reaching_tts(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    providers = FaceTaggedProviders()
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-FACE-CONTROL")
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    messages: list[dict[str, object]] = []
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})
        while True:
            message = websocket.receive()
            if message.get("bytes") is not None:
                continue
            payload = json.loads(message["text"])
            messages.append(payload)
            if payload.get("type") == "tts" and payload.get("state") == "stop":
                break

    llm_emotions = [
        str(message["emotion"]) for message in messages if message.get("type") == "llm"
    ]
    assert llm_emotions == ["thinking", "happy", "curious"]
    assert providers.spoken_texts == ["今天会很顺利。", "还想聊点什么？"]
    assert not any("[[face:" in text for text in providers.spoken_texts)
