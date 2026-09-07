import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import Agent, AuditEvent, Claim, Device, Entitlement, UsageEvent
from backend.app.quota import _as_utc
from backend.app.routers import devices
from backend.app.security import hash_secret


def _login(client: TestClient, name: str) -> dict[str, str]:
    response = client.post("/v1/auth/dev-login", json={"openid": name, "adult_confirmed": True})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _new_device(client: TestClient, admin_headers: dict[str, str], serial: str) -> dict:
    response = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": serial, "board_type": "hensun-desk-v1"},
    )
    assert response.status_code == 200, response.text
    return {**response.json(), "serial": serial}


def _claim_code(client: TestClient, device: dict) -> str:
    response = client.post(
        "/v1/device/bootstrap",
        headers={
            "Device-Id": device["serial"],
            "Authorization": f"Bearer {device['device_secret']}",
        },
        json={"firmware_version": "2.4.2"},
    )
    assert response.status_code == 200, response.text
    return response.json()["claim_code"]


def _confirm(client: TestClient, headers: dict, code: str, legacy: bool = False):
    path = "/v1/claims/confirm-phone" if legacy else "/v1/claims/confirm"
    return client.post(path, headers=headers, json={"claim_code": code})


def _quota(client: TestClient, headers: dict) -> dict:
    response = client.get("/v1/account/entitlement", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _rows(client: TestClient, model):
    async with client.app.state.session_factory() as session:
        return list(await session.scalars(select(model)))


def test_each_new_device_extends_account_by_thirty_days_without_signup_bonus(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    headers = _login(client, "gift-stack")
    assert _quota(client, headers)["plan"] == "free"
    assert client.get("/v1/onboarding/status", headers=headers).status_code == 200
    assert asyncio.run(_rows(client, Entitlement)) == []
    first = _new_device(client, admin_headers, "GIFT-STACK-1")
    first_code = _claim_code(client, first)
    before = datetime.now(UTC)
    assert _confirm(client, headers, first_code).status_code == 200
    first_quota = _quota(client, headers)
    first_expiry = datetime.fromisoformat(first_quota["expires_at"])
    assert before + timedelta(days=30) <= first_expiry <= datetime.now(UTC) + timedelta(days=30)

    second = _new_device(client, admin_headers, "GIFT-STACK-2")
    assert _confirm(client, headers, _claim_code(client, second), legacy=True).status_code == 200
    assert datetime.fromisoformat(_quota(client, headers)["expires_at"]) == (
        first_expiry + timedelta(days=30)
    )
    assert len(asyncio.run(_rows(client, Entitlement))) == 1
    for _ in range(2):
        assert client.get("/v1/onboarding/status", headers=headers).status_code == 200
    assert _confirm(client, headers, first_code).status_code == 404
    assert datetime.fromisoformat(_quota(client, headers)["expires_at"]) == (
        first_expiry + timedelta(days=30)
    )


def test_unbinding_reclaiming_and_transfer_never_reissue_a_device_gift(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owner = _login(client, "gift-original-owner")
    recipient = _login(client, "gift-recipient")
    device = _new_device(client, admin_headers, "GIFT-TRANSFER")
    assert _confirm(client, owner, _claim_code(client, device)).status_code == 200
    original_quota = _quota(client, owner)
    for claimant in (owner, recipient):
        assert (
            client.post(f"/v1/devices/{device['device_id']}/unbind", headers=owner).status_code
            == 200
        )
        assert _confirm(client, claimant, _claim_code(client, device)).status_code == 200
    assert _quota(client, owner) == original_quota
    assert _quota(client, recipient)["plan"] == "free"
    assert len(asyncio.run(_rows(client, Entitlement))) == 1


@pytest.mark.parametrize("expired", [False, True])
def test_gift_preserves_active_plan_and_usage_or_starts_after_expiry(
    client: TestClient, admin_headers: dict[str, str], expired: bool
) -> None:
    headers = _login(client, "gift-plan")
    first = _new_device(client, admin_headers, "GIFT-PLAN-1")
    assert _confirm(client, headers, _claim_code(client, first)).status_code == 200
    old_expiry = datetime.now(UTC) + timedelta(days=-2 if expired else 90)

    async def set_plan() -> None:
        async with client.app.state.session_factory() as session:
            entitlement = await session.scalar(select(Entitlement))
            entitlement.plan = "paid-standard"
            entitlement.monthly_turn_limit = 1234
            entitlement.expires_at = old_expiry
            session.add(
                UsageEvent(user_id=entitlement.user_id, device_id=first["device_id"], quantity=17)
            )
            await session.commit()

    asyncio.run(set_plan())
    second = _new_device(client, admin_headers, "GIFT-PLAN-2")
    before = datetime.now(UTC)
    assert _confirm(client, headers, _claim_code(client, second)).status_code == 200
    quota = _quota(client, headers)
    assert quota["used_turns"] == 17
    expiry = datetime.fromisoformat(quota["expires_at"])
    if expired:
        assert quota["plan"] == "trial"
        assert quota["monthly_turn_limit"] == 600
        assert before + timedelta(days=30) <= expiry <= datetime.now(UTC) + timedelta(days=30)
        assert len(asyncio.run(_rows(client, Entitlement))) == 2
    else:
        assert quota["plan"] == "paid-standard"
        assert quota["monthly_turn_limit"] == 1234
        assert expiry == old_expiry + timedelta(days=30)
        assert len(asyncio.run(_rows(client, Entitlement))) == 1
    assert quota["remaining_turns"] == quota["monthly_turn_limit"] - 17


@pytest.mark.parametrize("same_code", [False, True])
def test_concurrent_claims_for_one_device_grant_once(
    client: TestClient, admin_headers: dict[str, str], same_code: bool
) -> None:
    headers = [_login(client, f"gift-race-{index}") for index in range(2)]
    device = _new_device(client, admin_headers, "GIFT-RACE")
    first_code = _claim_code(client, device)
    second_code = first_code
    if not same_code:
        second_code = f"{(int(first_code) + 1) % 1_000_000:06d}"

        async def add_legacy_code() -> None:
            async with client.app.state.session_factory() as session:
                session.add(Claim(
                    device_id=device["device_id"],
                    code_hash=hash_secret(
                        second_code, client.app.state.settings.device_credential_pepper
                    ),
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                ))
                await session.commit()

        asyncio.run(add_legacy_code())
    codes = [first_code, second_code]
    barrier = Barrier(2)

    def claim(index: int):
        barrier.wait(timeout=10)
        return _confirm(client, headers[index], codes[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(claim, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 404]
    assert len(asyncio.run(_rows(client, Entitlement))) == 1
    assert sorted(_quota(client, header)["plan"] for header in headers) == ["free", "trial"]


def test_concurrent_new_devices_stack_for_one_account(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    headers = _login(client, "gift-race-account")
    devices_to_claim = [
        _new_device(client, admin_headers, f"GIFT-ACCOUNT-RACE-{index}") for index in range(2)
    ]
    codes = [_claim_code(client, device) for device in devices_to_claim]
    before = datetime.now(UTC)
    barrier = Barrier(2)

    def claim(code: str):
        barrier.wait(timeout=10)
        return _confirm(client, headers, code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(claim, codes))
    assert [response.status_code for response in responses] == [200, 200]
    expiry = datetime.fromisoformat(_quota(client, headers)["expires_at"])
    assert before + timedelta(days=60) <= expiry <= datetime.now(UTC) + timedelta(days=60)
    assert len(asyncio.run(_rows(client, Entitlement))) == 1
    assert len(asyncio.run(_rows(client, Agent))) == 1


def test_claim_and_gift_roll_back_together_then_retry_can_succeed(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "gift-rollback")
    device = _new_device(client, admin_headers, "GIFT-ROLLBACK")
    code = _claim_code(client, device)
    original_audit = devices.add_audit_event

    def fail_claim_audit(*args, **kwargs):
        if kwargs.get("action") == "device.claimed":
            raise RuntimeError("forced failure before claim commit")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(devices, "add_audit_event", fail_claim_audit)
    with pytest.raises(RuntimeError, match="forced failure"):
        _confirm(client, headers, code)
    stored_device = asyncio.run(_rows(client, Device))[0]
    assert stored_device.owner_user_id is None
    assert stored_device.service_gift_status == "eligible"
    assert stored_device.service_gift_granted_at is None
    assert asyncio.run(_rows(client, Entitlement)) == []
    assert asyncio.run(_rows(client, Claim))[0].consumed_at is None
    monkeypatch.setattr(devices, "add_audit_event", original_audit)
    assert _confirm(client, headers, code).status_code == 200
    stored_device = asyncio.run(_rows(client, Device))[0]
    assert stored_device.service_gift_status == "granted"
    assert _as_utc(stored_device.service_gift_granted_at) <= datetime.now(UTC)
    audits = asyncio.run(_rows(client, AuditEvent))
    assert sum(event.action == "device.service-gift-granted" for event in audits) == 1


def test_previously_activated_legacy_device_can_be_claimed_without_a_new_gift(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    headers = _login(client, "gift-legacy")
    device = _new_device(client, admin_headers, "GIFT-LEGACY")

    async def mark_legacy() -> None:
        async with client.app.state.session_factory() as session:
            stored = await session.get(Device, device["device_id"])
            stored.service_gift_status = "legacy-consumed"
            await session.commit()

    asyncio.run(mark_legacy())
    assert _confirm(client, headers, _claim_code(client, device)).status_code == 200
    assert _quota(client, headers)["plan"] == "free"
    assert asyncio.run(_rows(client, Entitlement)) == []
    stored = asyncio.run(_rows(client, Device))[0]
    assert stored.service_gift_status == "legacy-consumed"
    assert stored.service_gift_granted_at is None
