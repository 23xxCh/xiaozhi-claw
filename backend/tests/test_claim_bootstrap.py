import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import Claim
from backend.app.security import hash_secret


def _register(client: TestClient, admin_headers: dict[str, str], serial: str):
    response = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": serial, "board_type": "hensun-nocam-pilot-v1"},
    )
    assert response.status_code == 200, response.text
    return response.json()["device_id"], {
        "Device-Id": serial,
        "Authorization": f"Bearer {response.json()['device_secret']}",
    }


def _bootstrap(client: TestClient, headers: dict[str, str], endpoint: str):
    response = client.post(
        f"/v1/device/{endpoint}",
        headers=headers,
        json={"firmware_version": "2.4.2", "application": {"version": "2.4.2"}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    code = payload["activation"]["code"] if "activation" in payload else payload["claim_code"]
    return code, payload


def _login(client: TestClient, openid: str) -> dict[str, str]:
    response = client.post(
        "/v1/auth/dev-login", json={"openid": openid, "adult_confirmed": True}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.parametrize("first_endpoint", ["bootstrap", "xiaozhi-bootstrap"])
def test_bootstrap_retry_keeps_scanned_code_valid(
    client: TestClient, admin_headers: dict[str, str], first_endpoint: str
) -> None:
    device_id, headers = _register(client, admin_headers, "HENSUN-RETRY")
    first_code, _ = _bootstrap(client, headers, first_endpoint)
    for endpoint in ("xiaozhi-bootstrap", "bootstrap", "xiaozhi-bootstrap"):
        code, _ = _bootstrap(client, headers, endpoint)
        assert code == first_code
    claimed = client.post(
        "/v1/claims/confirm",
        headers=_login(client, "wx-retry-claim"),
        json={"claim_code": first_code},
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["id"] == device_id


def test_reused_code_keeps_original_expiry_and_rotates_only_after_expiry(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    device_id, headers = _register(client, admin_headers, "HENSUN-EXPIRY-RETRY")
    code, _ = _bootstrap(client, headers, "xiaozhi-bootstrap")

    async def set_expiry(expires_at: datetime) -> None:
        async with client.app.state.session_factory() as session:
            claim = await session.scalar(select(Claim).where(Claim.device_id == device_id))
            claim.expires_at = expires_at
            await session.commit()

    asyncio.run(set_expiry(datetime.now(UTC) + timedelta(seconds=200)))
    repeated, payload = _bootstrap(client, headers, "xiaozhi-bootstrap")
    assert repeated == code
    assert 190_000 <= payload["activation"]["timeout_ms"] <= 200_000
    asyncio.run(set_expiry(datetime.now(UTC) - timedelta(seconds=1)))
    replacement, _ = _bootstrap(client, headers, "bootstrap")
    assert replacement != code
    user_headers = _login(client, "wx-expiry-retry")
    assert client.post(
        "/v1/claims/confirm", headers=user_headers, json={"claim_code": code}
    ).status_code == 410
    assert client.post(
        "/v1/claims/confirm", headers=user_headers, json={"claim_code": replacement}
    ).status_code == 200


def test_legacy_random_code_stays_valid_when_reusable_code_is_added(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    device_id, headers = _register(client, admin_headers, "HENSUN-LEGACY-VALID")
    legacy_code = "182736"
    expiry = datetime.now(UTC) + timedelta(minutes=3)

    async def seed_legacy() -> None:
        async with client.app.state.session_factory() as session:
            session.add(Claim(
                device_id=device_id,
                code_hash=hash_secret(
                    legacy_code, client.app.state.settings.device_credential_pepper
                ),
                expires_at=expiry,
            ))
            await session.commit()

    asyncio.run(seed_legacy())
    code, _ = _bootstrap(client, headers, "xiaozhi-bootstrap")
    for _ in range(3):
        assert _bootstrap(client, headers, "bootstrap")[0] == code

    async def check_rows() -> None:
        async with client.app.state.session_factory() as session:
            claims = list(await session.scalars(select(Claim).where(Claim.device_id == device_id)))
            assert len(claims) == 2
            legacy = next(claim for claim in claims if claim.code_hash == hash_secret(
                legacy_code, client.app.state.settings.device_credential_pepper
            ))
            assert legacy.consumed_at is None
            assert legacy.expires_at.replace(tzinfo=UTC) == expiry

    asyncio.run(check_rows())
    response = client.post(
        "/v1/claims/confirm",
        headers=_login(client, "wx-legacy-valid"),
        json={"claim_code": legacy_code},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == device_id


def test_code_collision_retry_is_bounded_and_cannot_claim_another_device(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app import claims

    first_id, first_headers = _register(client, admin_headers, "HENSUN-COLLISION-FIRST")
    original_code, _ = _bootstrap(client, first_headers, "bootstrap")
    second_id, second_headers = _register(client, admin_headers, "HENSUN-COLLISION-SECOND")
    derive = claims._claim_code
    attempts = 0

    def collide_once(claim_id: str, device_id: str, pepper: str) -> str:
        nonlocal attempts
        attempts += 1
        return original_code if attempts == 1 else derive(claim_id, device_id, pepper)

    monkeypatch.setattr(claims, "_claim_code", collide_once)
    second_code, _ = _bootstrap(client, second_headers, "bootstrap")
    assert attempts == 2
    assert second_code != original_code
    for code, device_id, login in (
        (original_code, first_id, "wx-collision-first"),
        (second_code, second_id, "wx-collision-second"),
    ):
        response = client.post(
            "/v1/claims/confirm", headers=_login(client, login), json={"claim_code": code}
        )
        assert response.status_code == 200, response.text
        assert response.json()["id"] == device_id

    _, third_headers = _register(client, admin_headers, "HENSUN-COLLISION-EXHAUSTED")
    attempts = 0

    def always_collide(*args: str) -> str:
        nonlocal attempts
        attempts += 1
        return original_code

    monkeypatch.setattr(claims, "_claim_code", always_collide)
    response = client.post(
        "/v1/device/xiaozhi-bootstrap", headers=third_headers, json={}
    )
    assert response.status_code == 503
    assert attempts == 8


def test_concurrent_bootstraps_issue_one_code(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    device_id, headers = _register(client, admin_headers, "HENSUN-CONCURRENT-BOOT")
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(
            lambda endpoint: _bootstrap(client, headers, endpoint)[0],
            ["bootstrap", "xiaozhi-bootstrap"] * 2,
        ))
    assert len(set(codes)) == 1

    async def check_rows() -> None:
        async with client.app.state.session_factory() as session:
            rows = list(await session.scalars(select(Claim).where(Claim.device_id == device_id)))
            assert len(rows) == 1

    asyncio.run(check_rows())


@pytest.mark.parametrize("legacy_already_present", [True, False])
def test_claiming_then_unbinding_revokes_other_legacy_codes(
    client: TestClient, admin_headers: dict[str, str], legacy_already_present: bool
) -> None:
    device_id, headers = _register(client, admin_headers, "HENSUN-LEGACY-REPLAY")
    code, _ = _bootstrap(client, headers, "bootstrap")
    legacy_code = f"{(int(code) + 1) % 1_000_000:06d}"

    async def add_legacy_claim() -> None:
        async with client.app.state.session_factory() as session:
            session.add(
                Claim(
                    code_hash=hash_secret(
                        legacy_code, client.app.state.settings.device_credential_pepper
                    ),
                    device_id=device_id,
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                )
            )
            await session.commit()

    if legacy_already_present:
        asyncio.run(add_legacy_claim())
    owner_headers = _login(client, "wx-first-owner")
    claimed = client.post(
        "/v1/claims/confirm", headers=owner_headers, json={"claim_code": code}
    )
    assert claimed.status_code == 200, claimed.text
    if not legacy_already_present:
        asyncio.run(add_legacy_claim())
    assert client.post(f"/v1/devices/{device_id}/unbind", headers=owner_headers).status_code == 200
    replay = client.post(
        "/v1/claims/confirm",
        headers=_login(client, "wx-new-owner"),
        json={"claim_code": legacy_code},
    )
    assert replay.status_code == 404, replay.text

    async def assert_all_consumed() -> None:
        async with client.app.state.session_factory() as session:
            claims = list(await session.scalars(select(Claim).where(Claim.device_id == device_id)))
            assert all(claim.consumed_at is not None for claim in claims)

    asyncio.run(assert_all_consumed())


def test_rma_reset_revokes_outstanding_code(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    from .test_staff_roles import _staff_token

    device_id, headers = _register(client, admin_headers, "HENSUN-RMA-CODE")
    code, _ = _bootstrap(client, headers, "xiaozhi-bootstrap")
    support = _staff_token(client, admin_headers, username="support-rma", role="support")
    response = client.post(
        f"/v1/admin/devices/{device_id}/rma",
        headers={"Authorization": f"Bearer {support}"},
        json={"reason": "claim invalidation regression", "confirm": True},
    )
    assert response.status_code == 200, response.text
    claim = client.post(
        "/v1/claims/confirm",
        headers=_login(client, "wx-rma-claim"),
        json={"claim_code": code},
    )
    assert claim.status_code == 404
    blocked = client.post("/v1/device/xiaozhi-bootstrap", headers=headers, json={})
    assert blocked.status_code == 423
