from fastapi.testclient import TestClient


def _staff_token(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    username: str,
    role: str,
) -> str:
    created = client.post(
        "/v1/admin/staff",
        headers=admin_headers,
        json={"username": username, "display_name": username, "role": role},
    )
    assert created.status_code == 200, created.text
    login = client.post(
        "/v1/admin/auth/login",
        headers=admin_headers,
        json={"username": username},
    )
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def test_factory_and_support_roles_cannot_cross_privilege_boundaries(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    factory = _staff_token(
        client,
        admin_headers,
        username="factory-one",
        role="factory",
    )
    support = _staff_token(
        client,
        admin_headers,
        username="support-one",
        role="support",
    )
    factory_headers = {"Authorization": f"Bearer {factory}"}
    support_headers = {"Authorization": f"Bearer {support}"}

    registered = client.post(
        "/v1/admin/devices",
        headers=factory_headers,
        json={"serial_number": "HENSUN-ROLE-01", "board_type": "hensun-desk-v1"},
    )
    assert registered.status_code == 200, registered.text

    forbidden_registration = client.post(
        "/v1/admin/devices",
        headers=support_headers,
        json={"serial_number": "HENSUN-ROLE-02", "board_type": "hensun-desk-v1"},
    )
    assert forbidden_registration.status_code == 403

    forbidden_command = client.post(
        "/v1/admin/device-events/HENSUN-ROLE-01",
        headers=factory_headers,
        json={"event": "happy"},
    )
    assert forbidden_command.status_code == 403

    support_command = client.post(
        "/v1/admin/device-events/HENSUN-ROLE-01",
        headers=support_headers,
        json={"event": "happy"},
    )
    assert support_command.status_code == 200
    assert support_command.json()["queued"] is True


def test_engineering_can_manage_model_routes_only_with_confirmation(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    engineering = _staff_token(
        client,
        admin_headers,
        username="engineer-one",
        role="engineering",
    )
    headers = {"Authorization": f"Bearer {engineering}"}
    presets = client.get("/v1/admin/model-presets", headers=headers)
    assert presets.status_code == 200, presets.text
    assert presets.json()[0]["asr_model"]

    unconfirmed = client.patch(
        "/v1/admin/model-presets/fast-chat",
        headers=headers,
        json={"description": "new route"},
    )
    assert unconfirmed.status_code == 422

    confirmed = client.patch(
        "/v1/admin/model-presets/fast-chat",
        headers=headers,
        json={"description": "new route", "confirm": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["description"] == "new route"
