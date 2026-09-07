import pytest
from fastapi.testclient import TestClient

from .conftest import provision_owned_device


def test_active_release_is_served_to_authenticated_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    release = client.post(
        "/v1/ota/releases",
        headers=admin_headers,
        json={
            "board_type": "hensun-desk-v1",
            "version": "2.4.3",
            "artifact_url": "https://downloads.example.com/hensun-desk-v1-2.4.3.bin",
            "sha256": "a" * 64,
            "signature": "development-signature-placeholder",
            "rollout_percent": 100,
            "mandatory": False,
            "active": True,
            "confirm": True,
        },
    )
    assert release.status_code == 200, release.text

    checked = client.get(
        "/v1/ota/check?current_version=2.4.2",
        headers={
            "Device-Id": owned["serial"],
            "Authorization": f"Bearer {owned['device_secret']}",
        },
    )
    assert checked.status_code == 200
    assert checked.json()["version"] == "2.4.3"
    assert checked.json()["sha256"] == "a" * 64


def test_firmware_artifact_must_use_https(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/v1/ota/releases",
        headers=admin_headers,
        json={
            "board_type": "hensun-desk-v1",
            "version": "2.4.3",
            "artifact_url": "http://downloads.example.com/firmware.bin",
            "sha256": "a" * 64,
            "signature": "development-signature-placeholder",
            "rollout_percent": 10,
            "active": False,
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize("endpoint", ["check", "xiaozhi-bootstrap"])
@pytest.mark.parametrize("mandatory", [False, True])
def test_disabled_auto_update_blocks_optional_release_at_both_query_entries(
    client: TestClient, admin_headers: dict[str, str], endpoint: str, mandatory: bool
) -> None:
    owned = provision_owned_device(client, admin_headers)
    headers = {"Authorization": f"Bearer {owned['user_token']}"}
    preference = client.patch(
        f"/v1/devices/{owned['device_id']}", headers=headers, json={"ota_auto_update": False}
    )
    assert preference.status_code == 200, preference.text
    registered = client.post(
        "/v1/ota/releases",
        headers=admin_headers,
        json={
            "board_type": "hensun-desk-v1",
            "version": "2.4.3",
            "artifact_url": "https://downloads.example.com/firmware.bin",
            "sha256": "b" * 64,
            "signature": "development-signature-placeholder",
            "rollout_percent": 100,
            "mandatory": mandatory,
            "active": True,
            "confirm": True,
        },
    )
    assert registered.status_code == 200, registered.text
    device_headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }

    def query():
        if endpoint == "check":
            return client.get("/v1/ota/check?current_version=2.4.2", headers=device_headers)
        return client.post(
            "/v1/device/xiaozhi-bootstrap",
            headers=device_headers,
            json={"application": {"version": "2.4.2"}},
        )

    checked = query()
    if endpoint == "check":
        assert checked.status_code == (200 if mandatory else 204), checked.text
        if mandatory:
            assert checked.json()["mandatory"] is True
    else:
        assert checked.status_code == 200, checked.text
        assert "websocket" in checked.json()
        assert ("firmware" in checked.json()) is mandatory
        if mandatory:
            assert checked.json()["firmware"]["force"] == 1

    if not mandatory:
        enabled = client.patch(
            f"/v1/devices/{owned['device_id']}", headers=headers, json={"ota_auto_update": True}
        )
        assert enabled.status_code == 200
        checked = query()
        assert checked.status_code == 200
        firmware = checked.json() if endpoint == "check" else checked.json()["firmware"]
        assert firmware["version"] == "2.4.3"
