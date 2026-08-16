from fastapi.testclient import TestClient


def test_api_errors_use_stable_user_safe_envelope(client: TestClient) -> None:
    response = client.get("/v1/devices")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    assert response.json()["message"] == "登录状态已失效，请重新登录"
    assert response.json()["request_id"] == response.headers["X-Request-Id"]
    assert "bearer" not in response.text.lower()


def test_validation_errors_do_not_expose_internal_validation_details(client: TestClient) -> None:
    response = client.post("/v1/auth/dev-login", json={"openid": "x", "adult_confirmed": True})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "input" not in response.text.lower()
