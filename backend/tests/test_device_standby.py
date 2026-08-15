from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.models import DeviceCommand

from .conftest import provision_owned_device


def _user_headers(owned: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {owned['user_token']}"}


def _device_headers(owned: dict[str, str]) -> dict[str, str]:
    return {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }


def test_online_standby_waits_for_device_ack(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json(
            {"type": "hello", "version": 1, "device_config_schema_version": 2}
        )
        websocket.receive_json()

        requested = client.post(
            f"/v1/devices/{owned['device_id']}/standby",
            headers=_user_headers(owned),
        )
        assert requested.status_code == 202, requested.text
        assert requested.json()["status"] == "delivered"

        command = websocket.receive_json()
        assert command == {
            "session_id": command["session_id"],
            "type": "system",
            "command": "enter_standby",
            "command_id": requested.json()["command_id"],
        }

        delivered = client.get(
            f"/v1/devices/{owned['device_id']}/commands/{command['command_id']}",
            headers=_user_headers(owned),
        )
        assert delivered.status_code == 200
        assert delivered.json()["status"] == "delivered"

        websocket.send_json(
            {"type": "device_state", "state": "standby", "reason": "remote"}
        )
        websocket.send_json(
            {
                "type": "device_command_ack",
                "command_id": command["command_id"],
                "status": "applied",
            }
        )
        websocket.send_json({"type": "hello", "version": 1})
        websocket.receive_json()

        applied = client.get(
            f"/v1/devices/{owned['device_id']}/commands/{command['command_id']}",
            headers=_user_headers(owned),
        )
        assert applied.status_code == 200
        assert applied.json()["status"] == "applied"

        device = client.get("/v1/devices", headers=_user_headers(owned)).json()[0]
        assert device["runtime_state"] == "standby"
        assert device["runtime_reason"] == "remote"
        assert device["runtime_state_at"] is not None


def test_expired_offline_standby_is_not_reported_as_success(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    requested = client.post(
        f"/v1/devices/{owned['device_id']}/standby",
        headers=_user_headers(owned),
    )
    assert requested.status_code == 202, requested.text
    assert requested.json()["status"] == "pending"

    async def expire_command() -> None:
        async with client.app.state.session_factory() as session:
            command = await session.get(DeviceCommand, requested.json()["command_id"])
            assert command is not None
            command.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    client.portal.call(expire_command)
    status = client.get(
        f"/v1/devices/{owned['device_id']}/commands/{requested.json()['command_id']}",
        headers=_user_headers(owned),
    )
    assert status.status_code == 200
    assert status.json()["status"] == "expired"
