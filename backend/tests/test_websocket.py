from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from backend.realtime.providers import TranscriptionResult

from .conftest import provision_owned_device


class FixedAsrSession:
    async def send_audio(self, frame: bytes) -> None:
        del frame

    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult("测试多帧语音", "happy")

    async def cancel(self) -> None:
        return None


class FixedStreamingLlm:
    async def reply_stream(
        self,
        transcript: str,
        history: list[dict[str, str]],
        memories: list[str],
        *,
        system_prompt: str,
        model: str,
        temperature: float,
    ) -> AsyncIterator[str]:
        del transcript, history, memories, system_prompt, model, temperature
        yield "多帧回复。"


class MultiFrameTtsSession:
    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        del text
        yield b"opus-frame-1"
        yield b"opus-frame-2"

    async def finish(self) -> None:
        return None

    async def cancel(self) -> None:
        return None


class MultiFrameRealtimeProviders:
    mock = True
    llm = FixedStreamingLlm()

    async def open_asr(self) -> FixedAsrSession:
        return FixedAsrSession()

    async def open_tts(
        self, voice: str, speech_rate: float = 1.0
    ) -> MultiFrameTtsSession:
        del voice, speech_rate
        return MultiFrameTtsSession()


class ServerEndpointAsrSession(FixedAsrSession):
    def __init__(self) -> None:
        self.frames = 0

    async def send_audio(self, frame: bytes) -> None:
        del frame
        self.frames += 1

    def endpoint_detected(self) -> bool:
        return self.frames >= 2


class ServerEndpointProviders(MultiFrameRealtimeProviders):
    def __init__(self) -> None:
        self.opened_asr_sessions = 0

    async def open_asr(self) -> ServerEndpointAsrSession:
        self.opened_asr_sessions += 1
        return ServerEndpointAsrSession()


def _device_headers(owned: dict[str, str]) -> dict[str, str]:
    return {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
        "Protocol-Version": "1",
    }


def _receive_mock_turn(websocket) -> tuple[dict[str, object], bytes]:
    stt = websocket.receive_json()
    assert stt["type"] == "stt"
    thinking = websocket.receive_json()
    assert thinking["type"] == "llm"
    reply_emotion = websocket.receive_json()
    assert reply_emotion["type"] == "llm"
    assert stt["turn_id"] == thinking["turn_id"] == reply_emotion["turn_id"]
    start = websocket.receive_json()
    assert start["state"] == "start"
    assert start["reply_id"]
    assert start["turn_id"] == stt["turn_id"]
    websocket.send_json({"type": "tts", "state": "ready", "reply_id": start["reply_id"]})
    sentence = websocket.receive_json()
    assert sentence["state"] == "sentence_start"
    audio = websocket.receive_bytes()
    stop = websocket.receive_json()
    assert stop["type"] == "tts"
    assert stop["state"] == "stop"
    assert stop["reply_id"] == start["reply_id"]
    assert stop["turn_id"] == start["turn_id"]
    websocket.send_json({"type": "tts", "state": "drained", "reply_id": start["reply_id"]})
    return stt, audio


def test_mock_voice_turn_uses_xiaozhi_message_shapes(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        hello = websocket.receive_json()
        assert hello["type"] == "hello"
        assert hello["audio_params"]["format"] == "mock-utf8"
        assert "AI" in hello["disclosure"]
        assert hello["session_id"]

        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("今天有什么安排".encode())
        websocket.send_json({"type": "listen", "state": "stop"})

        stt, audio = _receive_mock_turn(websocket)
        assert stt["text"] == "今天有什么安排"
        assert audio.decode() == "收到：今天有什么安排"

    entitlement = client.get(
        "/v1/account/entitlement",
        headers={"Authorization": f"Bearer {owned['user_token']}"},
    )
    assert entitlement.json()["used_turns"] == 1
    assert entitlement.json()["remaining_turns"] == 599


def test_admin_can_push_whitelisted_face_events_to_an_online_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        hello = websocket.receive_json()

        sent = client.post(
            f"/v1/admin/device-events/{owned['serial']}",
            headers=admin_headers,
            json={
                "event": "reminder",
                "message_type": "alert",
                "status": "提醒",
                "message": "该休息一下了",
            },
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["delivered"] is True
        event = websocket.receive_json()
        assert event == {
            "session_id": hello["session_id"],
            "type": "alert",
            "status": "提醒",
            "message": "该休息一下了",
            "emotion": "reminder",
        }

        sent = client.post(
            f"/v1/admin/device-events/{owned['serial']}",
            headers=admin_headers,
            json={"event": "happy"},
        )
        assert sent.status_code == 200, sent.text
        event = websocket.receive_json()
        assert event["type"] == "llm"
        assert event["emotion"] == "happy"
        assert event["session_id"] == hello["session_id"]


def test_device_receives_each_streamed_audio_frame(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = MultiFrameRealtimeProviders()
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["audio_params"]["format"] == "mock-utf8"
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"input-opus")
        websocket.send_json({"type": "listen", "state": "stop"})

        assert websocket.receive_json()["type"] == "stt"
        assert websocket.receive_json()["type"] == "llm"
        assert websocket.receive_json()["type"] == "llm"
        start = websocket.receive_json()
        assert start["state"] == "start"
        websocket.send_json(
            {"type": "tts", "state": "ready", "reply_id": start["reply_id"]}
        )
        assert websocket.receive_json()["state"] == "sentence_start"
        assert websocket.receive_bytes() == b"opus-frame-1"
        assert websocket.receive_bytes() == b"opus-frame-2"
        stop = websocket.receive_json()
        assert stop["state"] == "stop"
        assert stop["reply_id"] == start["reply_id"]
        websocket.send_json(
            {"type": "tts", "state": "drained", "reply_id": start["reply_id"]}
        )


def test_server_vad_finishes_turn_without_device_listen_stop(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    providers = ServerEndpointProviders()
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-SERVER-VAD")
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"first-frame")
        websocket.send_bytes(b"second-frame")

        assert websocket.receive_json()["type"] == "stt"
        assert websocket.receive_json()["type"] == "llm"
        assert websocket.receive_json()["type"] == "llm"
        start = websocket.receive_json()
        assert start["state"] == "start"
        websocket.send_json(
            {"type": "tts", "state": "ready", "reply_id": start["reply_id"]}
        )
        assert websocket.receive_json()["state"] == "sentence_start"
        assert websocket.receive_bytes() == b"opus-frame-1"
        assert websocket.receive_bytes() == b"opus-frame-2"
        stop = websocket.receive_json()
        websocket.send_json(
            {"type": "tts", "state": "drained", "reply_id": stop["reply_id"]}
        )

    assert providers.opened_asr_sessions == 1


def test_face_event_api_rejects_unknown_events_and_queues_offline_devices(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    unknown = client.post(
        f"/v1/admin/device-events/{owned['serial']}",
        headers=admin_headers,
        json={"event": "arbitrary-servo-command"},
    )
    assert unknown.status_code == 422

    offline = client.post(
        f"/v1/admin/device-events/{owned['serial']}",
        headers=admin_headers,
        json={"event": "happy"},
    )
    assert offline.status_code == 200
    assert offline.json()["delivered"] is False
    assert offline.json()["queued"] is True


def test_xiaozhi_bootstrap_token_can_open_device_websocket(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="02:00:00:00:00:01")
    bootstrap = client.post(
        "/v1/device/xiaozhi-bootstrap",
        headers={"Device-Id": owned["serial"], "Client-Id": "local-pilot"},
        json={"application": {"version": "2.4.2-local"}},
    )
    assert bootstrap.status_code == 200, bootstrap.text
    configuration = bootstrap.json()
    assert configuration["websocket"]["url"].endswith("/v1/device/ws")
    assert configuration["websocket"]["version"] == 1
    assert configuration["server_time"]["timezone_offset"] == 0

    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {configuration['websocket']['token']}",
        "Protocol-Version": "1",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "hello", "version": 1, "transport": "websocket"})
        assert websocket.receive_json()["type"] == "hello"


def test_xiaozhi_bootstrap_returns_six_digit_claim_code_for_unclaimed_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    registered = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": "02:00:00:00:00:02", "board_type": "hensun-cam-pilot-v1"},
    )
    assert registered.status_code == 200
    bootstrap = client.post(
        "/v1/device/xiaozhi-bootstrap",
        headers={"Device-Id": "02:00:00:00:00:02"},
        json={},
    )
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["activation"]["code"].isdigit()
    assert len(bootstrap.json()["activation"]["code"]) == 6
    assert bootstrap.json()["activation"]["timeout_ms"] == 600_000
