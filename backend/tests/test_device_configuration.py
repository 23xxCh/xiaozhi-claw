import json

from fastapi.testclient import TestClient

from backend.app.models import DeviceCommand, DeviceCommandStatus

from .conftest import provision_owned_device


def _user_headers(owned: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {owned['user_token']}"}


def _device_headers(owned: dict[str, str]) -> dict[str, str]:
    return {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }


def test_offline_device_configuration_is_versioned_and_queued(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    headers = _user_headers(owned)

    initial = client.get(f"/v1/devices/{owned['device_id']}/configuration", headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.json() == {
        "device_id": owned["device_id"],
        "desired_version": 0,
        "applied_version": 0,
        "speaker_volume": 70,
        "screen_brightness": 75,
        "applied_speaker_volume": None,
        "applied_screen_brightness": None,
        "sync_status": "unknown",
        "last_error_code": None,
        "command_id": None,
        "updated_at": initial.json()["updated_at"],
        "applied_at": None,
    }

    changed = client.patch(
        f"/v1/devices/{owned['device_id']}/configuration",
        headers=headers,
        json={"speaker_volume": 62, "screen_brightness": 48},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["desired_version"] == 1
    assert changed.json()["sync_status"] == "pending"

    async def assert_command() -> None:
        async with client.app.state.session_factory() as session:
            command = await session.get(DeviceCommand, changed.json()["command_id"])
            assert command is not None
            assert command.status == DeviceCommandStatus.PENDING.value
            payload = json.loads(command.payload_json)
            assert payload == {
                "type": "system",
                "command": "apply_config",
                "command_id": command.id,
                "config_version": 1,
                "config": {"speaker_volume": 62, "screen_brightness": 48},
            }

    client.portal.call(assert_command)


def test_device_configuration_rejects_values_that_will_not_survive_reboot(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    response = client.patch(
        f"/v1/devices/{owned['device_id']}/configuration",
        headers=_user_headers(owned),
        json={"speaker_volume": 0, "screen_brightness": 9},
    )
    assert response.status_code == 422


def test_online_device_ack_is_persisted_as_applied(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"

        changed = client.patch(
            f"/v1/devices/{owned['device_id']}/configuration",
            headers=_user_headers(owned),
            json={"speaker_volume": 66, "screen_brightness": 44},
        )
        assert changed.status_code == 200, changed.text
        command = websocket.receive_json()
        assert command["type"] == "system"
        assert command["command"] == "apply_config"
        assert command["config_version"] == 1

        websocket.send_json(
            {
                "type": "device_config_ack",
                "command_id": command["command_id"],
                "config_version": 1,
                "status": "applied",
                "applied": {"speaker_volume": 66, "screen_brightness": 44},
            }
        )
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"

    current = client.get(
        f"/v1/devices/{owned['device_id']}/configuration", headers=_user_headers(owned)
    )
    assert current.status_code == 200, current.text
    assert current.json()["sync_status"] == "synced"
    assert current.json()["applied_version"] == 1
    assert current.json()["applied_speaker_volume"] == 66
    assert current.json()["applied_screen_brightness"] == 44


def test_agent_runtime_parameters_are_bounded_and_versioned(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    headers = _user_headers(owned)
    agent = client.get("/v1/agents", headers=headers).json()[0]

    changed = client.patch(
        f"/v1/agents/{agent['id']}",
        headers=headers,
        json={"llm_temperature": 0.35, "tts_speech_rate": 1.2},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["llm_temperature"] == 0.35
    assert changed.json()["tts_speech_rate"] == 1.2
    assert changed.json()["config_version"] == agent["config_version"] + 1

    invalid = client.patch(
        f"/v1/agents/{agent['id']}",
        headers=headers,
        json={"llm_temperature": 2.1, "tts_speech_rate": 0.4},
    )
    assert invalid.status_code == 422
