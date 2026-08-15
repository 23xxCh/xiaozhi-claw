import json

from fastapi.testclient import TestClient

from backend.app.models import DeviceCommand, DeviceCommandStatus
from backend.generated.device_contracts import default_device_config

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
        "schema_version": 1,
        "values": default_device_config(1),
        "applied_values": None,
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
                "schema_version": 1,
                "values": {
                    **default_device_config(1),
                    "audio.speaker_volume": 62,
                    "display.brightness": 48,
                },
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
        websocket.send_json(
            {
                "type": "hello",
                "version": 1,
                "protocol_version": 1,
                "hardware_profile_id": "hensun-cam-pilot-v1",
                "display_profile_id": "st7789-320x240-landscape-v1",
                "profile_schema_version": 1,
                "profile_sha256": "a" * 64,
                "device_config_schema_version": 1,
            }
        )
        hello = websocket.receive_json()
        assert hello["type"] == "hello"
        assert hello["protocol_version"] == 1
        assert hello["device_config_schema_version"] == 2

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
        assert command["schema_version"] == 1

        websocket.send_json(
            {
                "type": "device_config_ack",
                "command_id": command["command_id"],
                "config_version": 1,
                "schema_version": 1,
                "status": "applied",
                "applied_values": command["values"],
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
    assert current.json()["applied_values"] == {
        **default_device_config(1),
        "audio.speaker_volume": 66,
        "display.brightness": 44,
    }
    device = client.get("/v1/devices", headers=_user_headers(owned)).json()[0]
    assert device["hardware_profile_id"] == "hensun-cam-pilot-v1"
    assert device["display_profile_id"] == "st7789-320x240-landscape-v1"
    assert device["profile_schema_version"] == 1


def test_customer_configuration_schema_hides_engineering_fields(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    response = client.get(
        f"/v1/devices/{owned['device_id']}/configuration-schema",
        headers=_user_headers(owned),
    )
    assert response.status_code == 200, response.text
    assert response.json()["schema_version"] == 1
    assert {field["key"] for field in response.json()["fields"]} == {
        "audio.speaker_volume",
        "display.brightness",
    }

    forbidden = client.patch(
        f"/v1/devices/{owned['device_id']}/configuration",
        headers=_user_headers(owned),
        json={"schema_version": 1, "values": {"audio.wake_threshold": 35}},
    )
    assert forbidden.status_code == 403


def test_schema_v2_exposes_and_applies_idle_timeout(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "version": 1,
                "protocol_version": 1,
                "device_config_schema_version": 2,
            }
        )
        assert websocket.receive_json()["device_config_schema_version"] == 2

        schema = client.get(
            f"/v1/devices/{owned['device_id']}/configuration-schema",
            headers=_user_headers(owned),
        )
        assert schema.status_code == 200, schema.text
        assert schema.json()["schema_version"] == 2
        assert {field["key"] for field in schema.json()["fields"]} == {
            "audio.speaker_volume",
            "display.brightness",
            "conversation.idle_timeout_seconds",
        }

        for timeout in (3, 10, 30):
            changed = client.patch(
                f"/v1/devices/{owned['device_id']}/configuration",
                headers=_user_headers(owned),
                json={
                    "schema_version": 2,
                    "values": {"conversation.idle_timeout_seconds": timeout},
                },
            )
            assert changed.status_code == 200, changed.text
            command = websocket.receive_json()
            assert command["schema_version"] == 2
            assert command["values"]["conversation.idle_timeout_seconds"] == timeout

        for timeout in (2, 31):
            rejected = client.patch(
                f"/v1/devices/{owned['device_id']}/configuration",
                headers=_user_headers(owned),
                json={
                    "schema_version": 2,
                    "values": {"conversation.idle_timeout_seconds": timeout},
                },
            )
            assert rejected.status_code == 422


def test_legacy_device_configuration_ack_remains_compatible(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        websocket.receive_json()
        changed = client.patch(
            f"/v1/devices/{owned['device_id']}/configuration",
            headers=_user_headers(owned),
            json={"speaker_volume": 61, "screen_brightness": 47},
        )
        command = websocket.receive_json()
        websocket.send_json(
            {
                "type": "device_config_ack",
                "command_id": command["command_id"],
                "config_version": changed.json()["desired_version"],
                "status": "applied",
                "applied": {"speaker_volume": 61, "screen_brightness": 47},
            }
        )
        websocket.send_json({"type": "hello", "version": 1})
        websocket.receive_json()

    current = client.get(
        f"/v1/devices/{owned['device_id']}/configuration",
        headers=_user_headers(owned),
    ).json()
    assert current["sync_status"] == "synced"
    assert current["applied_values"] == {
        "audio.speaker_volume": 61,
        "display.brightness": 47,
    }


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
