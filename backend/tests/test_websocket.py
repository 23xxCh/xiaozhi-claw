from fastapi.testclient import TestClient

from backend.app.providers import ProviderBundle

from .conftest import provision_owned_device


class MultiFrameSpeechProvider:
    async def transcribe(self, audio_frames: list[bytes]) -> str:
        return "测试多帧语音"

    async def synthesize(self, text: str) -> list[bytes]:
        return [b"opus-frame-1", b"opus-frame-2"]


class FixedLlmProvider:
    async def reply(self, text: str, memories: list[str]) -> str:
        return "多帧回复"


def test_mock_voice_turn_uses_xiaozhi_message_shapes(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
        "Protocol-Version": "1",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        hello = websocket.receive_json()
        assert hello["type"] == "hello"
        assert hello["audio_params"]["format"] == "mock-utf8"
        assert "AI" in hello["disclosure"]

        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("今天有什么安排".encode())
        websocket.send_json({"type": "listen", "state": "stop"})

        assert websocket.receive_json()["type"] == "stt"
        llm = websocket.receive_json()
        assert llm["type"] == "llm"
        assert "收到" in llm["text"]
        assert llm["emotion"] == "happy"
        assert websocket.receive_json() == {"type": "tts", "state": "start"}
        assert websocket.receive_bytes().decode() == llm["text"]
        assert websocket.receive_json() == {"type": "tts", "state": "stop"}

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
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
        "Protocol-Version": "1",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"

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
        assert websocket.receive_json() == {
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
        assert websocket.receive_json() == {"type": "llm", "emotion": "happy"}


def test_device_receives_each_normalized_opus_frame(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.providers = ProviderBundle(
        MultiFrameSpeechProvider(), FixedLlmProvider(), "opus"
    )
    owned = provision_owned_device(client, admin_headers)
    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
        "Protocol-Version": "1",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["audio_params"]["format"] == "opus"
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"input-opus")
        websocket.send_json({"type": "listen", "state": "stop"})

        assert websocket.receive_json()["type"] == "stt"
        assert websocket.receive_json()["type"] == "llm"
        assert websocket.receive_json() == {"type": "tts", "state": "start"}
        assert websocket.receive_bytes() == b"opus-frame-1"
        assert websocket.receive_bytes() == b"opus-frame-2"
        assert websocket.receive_json() == {"type": "tts", "state": "stop"}


def test_face_event_api_rejects_unknown_events_and_offline_devices(
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
    assert offline.status_code == 409
    assert offline.json()["detail"] == "device is offline"


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


def test_xiaozhi_bootstrap_rejects_unclaimed_device(
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
    assert bootstrap.status_code == 403
