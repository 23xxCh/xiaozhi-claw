from backend.app.safety import is_voice_standby_command
from backend.realtime.providers import TranscriptionResult

from .conftest import provision_owned_device


class VoiceStandbyAsr:
    async def send_audio(self, frame: bytes) -> None:
        del frame

    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult("小灿，闭嘴。", "neutral")

    async def cancel(self) -> None:
        return None


class VoiceStandbyProviders:
    mock = True

    async def open_asr(self) -> VoiceStandbyAsr:
        return VoiceStandbyAsr()

    @property
    def llm(self):
        raise AssertionError("voice standby must not call the LLM")

    async def open_tts(self, voice: str, speech_rate: float = 1.0):
        del voice, speech_rate
        raise AssertionError("voice standby must not call TTS")


def test_voice_standby_command_accepts_only_the_explicit_phrase() -> None:
    assert is_voice_standby_command("小灿闭嘴")
    assert is_voice_standby_command("小灿，闭嘴。")
    assert is_voice_standby_command("小 灿 闭 嘴")
    assert not is_voice_standby_command("请解释小灿闭嘴是什么意思")
    assert not is_voice_standby_command("闭嘴")


def test_voice_standby_skips_llm_and_tts(client, admin_headers) -> None:
    client.app.state.realtime_providers = VoiceStandbyProviders()
    owned = provision_owned_device(
        client,
        admin_headers,
        serial="HENSUN-VOICE-STANDBY",
    )
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"audio")
        websocket.send_json({"type": "listen", "state": "stop"})

        stt = websocket.receive_json()
        assert stt["type"] == "stt"
        assert stt["text"] == "小灿，闭嘴。"

        standby = websocket.receive_json()
        assert standby["type"] == "system"
        assert standby["command"] == "enter_standby"
        assert standby["command_id"].startswith("voice-standby-")
