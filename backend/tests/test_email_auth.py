import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import EmailLoginChallenge, User

from .conftest import provision_owned_device


def request_code(client: TestClient, email: str = "pilot@example.com") -> str:
    response = client.post("/v1/auth/email/request-code", json={"email": email})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["expires_in"] == 600
    assert payload["resend_after"] == 60
    assert len(payload["debug_code"]) == 6
    assert payload["debug_code"].isdigit()
    return payload["debug_code"]


def test_email_code_logs_in_new_user_without_skipping_agreements(client: TestClient) -> None:
    code = request_code(client, "New.User@Example.COM ")
    response = client.post(
        "/v1/auth/email/verify-code",
        json={"email": "new.user@example.com", "code": code},
    )
    assert response.status_code == 200, response.text
    assert response.json()["agreements_complete"] is False
    assert "hensun_session=" in response.headers["set-cookie"]

    me = client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "new.user@example.com"
    assert me.json()["adult_confirmed"] is False


def test_email_code_is_hashed_and_single_use(client: TestClient) -> None:
    code = request_code(client, "secure@example.com")

    async def load_challenge() -> EmailLoginChallenge:
        async with client.app.state.session_factory() as session:
            challenge = await session.scalar(
                select(EmailLoginChallenge).where(EmailLoginChallenge.email == "secure@example.com")
            )
            assert challenge is not None
            return challenge

    stored = asyncio.run(load_challenge())
    assert stored.code_hash != code
    assert code not in stored.code_hash

    wrong = client.post(
        "/v1/auth/email/verify-code",
        json={"email": "secure@example.com", "code": "000000"},
    )
    assert wrong.status_code == 400
    assert wrong.json()["code"] == "INVALID_CODE"

    success = client.post(
        "/v1/auth/email/verify-code",
        json={"email": "secure@example.com", "code": code},
    )
    assert success.status_code == 200
    reused = client.post(
        "/v1/auth/email/verify-code",
        json={"email": "secure@example.com", "code": code},
    )
    assert reused.status_code == 410
    assert reused.json()["code"] == "EXPIRED"


def test_email_code_expires_after_five_wrong_attempts(client: TestClient) -> None:
    code = request_code(client, "attempts@example.com")
    wrong_code = "000000" if code != "000000" else "111111"
    for _ in range(5):
        response = client.post(
            "/v1/auth/email/verify-code",
            json={"email": "attempts@example.com", "code": wrong_code},
        )
        assert response.status_code == 400
    blocked = client.post(
        "/v1/auth/email/verify-code",
        json={"email": "attempts@example.com", "code": code},
    )
    assert blocked.status_code == 410


def test_email_code_request_is_rate_limited(client: TestClient) -> None:
    request_code(client, "rate@example.com")
    response = client.post("/v1/auth/email/request-code", json={"email": "rate@example.com"})
    assert response.status_code == 429
    assert response.json()["code"] == "TOO_MANY_REQUESTS"


def test_production_email_code_response_never_exposes_debug_code(
    client: TestClient, monkeypatch
) -> None:
    client.app.state.settings.email_delivery_mode = "smtp"

    async def delivered(*_args) -> None:
        return None

    monkeypatch.setattr("backend.app.routers.auth.deliver_login_code", delivered)
    response = client.post(
        "/v1/auth/email/request-code", json={"email": "production@example.com"}
    )
    assert response.status_code == 200
    assert "debug_code" not in response.json()


def test_expired_email_code_is_rejected(client: TestClient) -> None:
    code = request_code(client, "expired@example.com")

    async def expire() -> None:
        async with client.app.state.session_factory() as session:
            challenge = await session.scalar(
                select(EmailLoginChallenge).where(
                    EmailLoginChallenge.email == "expired@example.com"
                )
            )
            assert challenge is not None
            challenge.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    asyncio.run(expire())
    response = client.post(
        "/v1/auth/email/verify-code",
        json={"email": "expired@example.com", "code": code},
    )
    assert response.status_code == 410


def test_development_email_login_links_the_single_legacy_account(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(
        client,
        admin_headers,
        serial="HENSUN-EMAIL-MIGRATION",
        openid="wx-email-legacy",
    )
    client.post("/v1/auth/logout")
    code = request_code(client, "owner@example.com")
    response = client.post(
        "/v1/auth/email/verify-code",
        json={"email": "owner@example.com", "code": code},
    )
    assert response.status_code == 200
    assert response.json()["agreements_complete"] is True

    async def load_user() -> User:
        async with client.app.state.session_factory() as session:
            user = await session.scalar(select(User).where(User.email == "owner@example.com"))
            assert user is not None
            return user

    user = asyncio.run(load_user())
    assert user.id
    assert user.wechat_openid == "wx-email-legacy"
    assert user.display_name == "owner"
    devices = client.get("/v1/devices")
    assert devices.status_code == 200
    assert any(item["id"] == owned["device_id"] for item in devices.json())
