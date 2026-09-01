import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import Claim
from backend.app.security import hash_secret

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


def test_factory_can_rotate_device_credential_once(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    registered = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": "HENSUN-ROTATE-01", "board_type": "hensun-desk-v1"},
    )
    assert registered.status_code == 200, registered.text
    original_secret = registered.json()["device_secret"]
    device_id = registered.json()["device_id"]

    missing_confirmation = client.post(
        f"/v1/admin/devices/{device_id}/rotate-credential",
        headers=admin_headers,
        json={"confirm": False},
    )
    assert missing_confirmation.status_code == 422

    rotated = client.post(
        f"/v1/admin/devices/{device_id}/rotate-credential",
        headers=admin_headers,
        json={"confirm": True},
    )
    assert rotated.status_code == 200, rotated.text
    rotated_secret = rotated.json()["device_secret"]
    assert rotated_secret != original_secret

    old_bootstrap = client.post(
        "/v1/device/bootstrap",
        headers={
            "Device-Id": "HENSUN-ROTATE-01",
            "Authorization": f"Bearer {original_secret}",
        },
        json={"firmware_version": "2.4.2"},
    )
    assert old_bootstrap.status_code == 401

    new_bootstrap = client.post(
        "/v1/device/bootstrap",
        headers={
            "Device-Id": "HENSUN-ROTATE-01",
            "Authorization": f"Bearer {rotated_secret}",
        },
        json={"firmware_version": "2.4.2"},
    )
    assert new_bootstrap.status_code == 200, new_bootstrap.text


def test_underage_development_login_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/dev-login", json={"openid": "wx-underage", "adult_confirmed": False}
    )
    assert response.status_code == 403


def test_owner_can_unbind_and_new_user_can_reclaim_same_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(
        client,
        admin_headers,
        serial="HENSUN-HANDOFF-01",
        openid="wx-handoff-owner",
    )
    owner_headers = {"Authorization": f"Bearer {owned['user_token']}"}
    consent = client.patch(
        f"/v1/devices/{owned['device_id']}/memory-consent",
        headers=owner_headers,
        json={"enabled": True},
    )
    assert consent.status_code == 200, consent.text

    unbound = client.post(f"/v1/devices/{owned['device_id']}/unbind", headers=owner_headers)
    assert unbound.status_code == 200, unbound.text
    assert unbound.json()["lifecycle"] == "factory-unclaimed"
    assert unbound.json()["reset_epoch"] == 1
    assert client.get("/v1/devices", headers=owner_headers).json() == []

    bootstrapped = client.post(
        "/v1/device/bootstrap",
        headers={
            "Device-Id": owned["serial"],
            "Authorization": f"Bearer {owned['device_secret']}",
        },
        json={"firmware_version": "2.4.2"},
    )
    assert bootstrapped.status_code == 200, bootstrapped.text
    assert bootstrapped.json()["lifecycle"] == "factory-unclaimed"
    assert len(bootstrapped.json()["claim_code"]) == 6

    new_login = client.post(
        "/v1/auth/dev-login",
        json={"openid": "wx-handoff-recipient", "adult_confirmed": True},
    )
    new_headers = {"Authorization": f"Bearer {new_login.json()['access_token']}"}
    reclaimed = client.post(
        "/v1/claims/confirm-phone",
        headers=new_headers,
        json={"claim_code": bootstrapped.json()["claim_code"]},
    )
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["memory_consent"] is False

    devices = client.get("/v1/devices", headers=new_headers)
    assert devices.status_code == 200
    assert len(devices.json()) == 1
    assert devices.json()[0]["active_agent_id"] is not None
    assert devices.json()[0]["active_profile_id"] is not None
    agents = client.get("/v1/agents", headers=new_headers)
    assert agents.status_code == 200
    assert agents.json()[0]["memory_consent"] is False


def test_non_owner_cannot_unbind_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(
        client,
        admin_headers,
        serial="HENSUN-HANDOFF-02",
        openid="wx-handoff-owner-02",
    )
    other_login = client.post(
        "/v1/auth/dev-login",
        json={"openid": "wx-handoff-other", "adult_confirmed": True},
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    response = client.post(f"/v1/devices/{owned['device_id']}/unbind", headers=other_headers)

    assert response.status_code == 404


def test_expired_claim_code_is_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    registered = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": "HENSUN-EXPIRED-01", "board_type": "hensun-desk-v1"},
    )
    device_secret = registered.json()["device_secret"]
    bootstrapped = client.post(
        "/v1/device/bootstrap",
        headers={
            "Device-Id": "HENSUN-EXPIRED-01",
            "Authorization": f"Bearer {device_secret}",
        },
        json={"firmware_version": "2.4.2"},
    )
    claim_code = bootstrapped.json()["claim_code"]

    async def expire_claim() -> None:
        async with client.app.state.session_factory() as session:
            claim = await session.scalar(
                select(Claim).where(
                    Claim.code_hash
                    == hash_secret(
                        claim_code,
                        client.app.state.settings.device_credential_pepper,
                    )
                )
            )
            assert claim is not None
            claim.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    asyncio.run(expire_claim())
    login = client.post(
        "/v1/auth/dev-login",
        json={"openid": "wx-expired-claim", "adult_confirmed": True},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.post(
        "/v1/claims/confirm-phone",
        headers=headers,
        json={"claim_code": claim_code},
    )

    assert response.status_code == 410


def test_second_valid_claim_code_cannot_take_an_owned_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    registered = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": "HENSUN-CLAIM-CONFLICT", "board_type": "hensun-desk-v1"},
    )
    device_secret = registered.json()["device_secret"]
    device_headers = {
        "Device-Id": "HENSUN-CLAIM-CONFLICT",
        "Authorization": f"Bearer {device_secret}",
    }
    first_code = client.post(
        "/v1/device/bootstrap",
        headers=device_headers,
        json={"firmware_version": "2.4.2"},
    ).json()["claim_code"]
    second_code = client.post(
        "/v1/device/bootstrap",
        headers=device_headers,
        json={"firmware_version": "2.4.2"},
    ).json()["claim_code"]
    first_login = client.post(
        "/v1/auth/dev-login",
        json={"openid": "wx-conflict-first", "adult_confirmed": True},
    )
    second_login = client.post(
        "/v1/auth/dev-login",
        json={"openid": "wx-conflict-second", "adult_confirmed": True},
    )
    first_headers = {"Authorization": f"Bearer {first_login.json()['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}
    assert (
        client.post(
            "/v1/claims/confirm-phone",
            headers=first_headers,
            json={"claim_code": first_code},
        ).status_code
        == 200
    )

    conflict = client.post(
        "/v1/claims/confirm-phone",
        headers=second_headers,
        json={"claim_code": second_code},
    )

    assert conflict.status_code == 409
