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
