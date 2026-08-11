from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def app(tmp_path) -> FastAPI:
    database = tmp_path / "test.db"
    return create_app(
        Settings(
            app_env="test",
            database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
            admin_api_key="test-admin-key",
            jwt_secret="test-jwt-secret-with-enough-entropy",
            device_credential_pepper="test-device-pepper-with-enough-entropy",
            memory_master_key="test-memory-key-with-enough-entropy",
            provider_mode="mock",
            ota_base_url="https://api.hensun.invalid/v1/ota/",
        )
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-Key": "test-admin-key"}


def provision_owned_device(
    client: TestClient,
    admin_headers: dict[str, str],
    *,
    serial: str = "HENSUN-000001",
    openid: str = "wx-test-adult",
) -> dict[str, str]:
    registered = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": serial, "board_type": "hensun-desk-v1"},
    )
    assert registered.status_code == 200, registered.text
    device_secret = registered.json()["device_secret"]

    bootstrapped = client.post(
        "/v1/device/bootstrap",
        headers={"Device-Id": serial, "Authorization": f"Bearer {device_secret}"},
        json={"firmware_version": "2.4.2"},
    )
    assert bootstrapped.status_code == 200, bootstrapped.text

    login = client.post("/v1/auth/dev-login", json={"openid": openid, "adult_confirmed": True})
    assert login.status_code == 200, login.text
    user_token = login.json()["access_token"]

    claimed = client.post(
        "/v1/claims/confirm-phone",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"claim_code": bootstrapped.json()["claim_code"]},
    )
    assert claimed.status_code == 200, claimed.text
    return {
        "serial": serial,
        "device_secret": device_secret,
        "device_id": claimed.json()["id"],
        "user_token": user_token,
    }
