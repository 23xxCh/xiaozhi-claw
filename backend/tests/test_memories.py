from fastapi.testclient import TestClient

from .conftest import provision_owned_device


def test_memory_is_opt_in_and_can_be_deleted(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    headers = {"Authorization": f"Bearer {owned['user_token']}"}
    path = f"/v1/devices/{owned['device_id']}/memories/preferred-name"
    payload = {"key": "preferred-name", "value": "用户希望被叫作小林"}

    rejected = client.put(path, headers=headers, json=payload)
    assert rejected.status_code == 409

    consent = client.patch(
        f"/v1/devices/{owned['device_id']}/memory-consent",
        headers=headers,
        json={"enabled": True},
    )
    assert consent.status_code == 200

    created = client.put(path, headers=headers, json=payload)
    assert created.status_code == 200
    assert created.json()["value"] == payload["value"]

    listed = client.get(f"/v1/devices/{owned['device_id']}/memories", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["key"] == "preferred-name"
    assert listed.json()[0]["value"] == payload["value"]

    deleted = client.delete(path, headers=headers)
    assert deleted.status_code == 204
    listed_again = client.get(f"/v1/devices/{owned['device_id']}/memories", headers=headers)
    assert listed_again.json() == []
