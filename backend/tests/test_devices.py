from fastapi.testclient import TestClient

from .conftest import provision_owned_device


def test_factory_registration_claim_and_trial(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    user_headers = {"Authorization": f"Bearer {owned['user_token']}"}

    devices = client.get("/v1/devices", headers=user_headers)
    assert devices.status_code == 200
    assert len(devices.json()) == 1
    device = devices.json()[0]
    assert device["id"] == owned["device_id"]
    assert device["serial_number"] == owned["serial"]
    assert device["board_type"] == "hensun-desk-v1"
    assert device["lifecycle"] == "owned"
    assert device["firmware_version"] == "2.4.2"
    assert device["name"] == "Hensun Desk"
    assert device["hardware_version"] == "v1"
    assert device["ota_auto_update"] is True
    assert device["active_agent_id"] is not None
    assert device["online"] is False

    entitlement = client.get("/v1/account/entitlement", headers=user_headers)
    assert entitlement.status_code == 200
    assert entitlement.json()["plan"] == "trial"
    assert entitlement.json()["monthly_turn_limit"] == 600
    assert entitlement.json()["remaining_turns"] == 600


def test_wrong_device_secret_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    registered = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": "HENSUN-000002", "board_type": "hensun-desk-v1"},
    )
    assert registered.status_code == 200
    response = client.post(
        "/v1/device/bootstrap",
        headers={"Device-Id": "HENSUN-000002", "Authorization": "Bearer wrong-secret"},
        json={"firmware_version": "2.4.2"},
    )
    assert response.status_code == 401


def test_underage_development_login_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/dev-login", json={"openid": "wx-underage", "adult_confirmed": False}
    )
    assert response.status_code == 403
