from __future__ import annotations

import argparse
import json

import httpx


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision one local Hensun pilot device")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--admin-key", default="development-admin-change-me")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    admin_headers = {"X-Admin-Key": args.admin_key}
    with httpx.Client(base_url=args.base_url, timeout=5) as client:
        registered = client.post(
            "/v1/admin/devices",
            headers=admin_headers,
            json={"serial_number": args.serial, "board_type": "hensun-cam-pilot-v1"},
        )
        if registered.status_code == 409:
            compatibility = client.post(
                "/v1/device/xiaozhi-bootstrap",
                headers={"Device-Id": args.serial},
                json={},
            )
            if compatibility.status_code == 200:
                print(json.dumps({"serial": args.serial, "status": "already-owned"}))
                return 0
            raise RuntimeError(
                "Device already exists but is not ready; use a fresh local database or recover it"
            )
        registered.raise_for_status()
        device_secret = registered.json()["device_secret"]

        bootstrapped = client.post(
            "/v1/device/bootstrap",
            headers={
                "Device-Id": args.serial,
                "Authorization": f"Bearer {device_secret}",
            },
            json={"firmware_version": "2.4.2-local"},
        )
        bootstrapped.raise_for_status()
        login = client.post(
            "/v1/auth/dev-login",
            json={"openid": f"lan-{args.serial}", "adult_confirmed": True},
        )
        login.raise_for_status()
        claimed = client.post(
            "/v1/claims/confirm-phone",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            json={"claim_code": bootstrapped.json()["claim_code"]},
        )
        claimed.raise_for_status()
        print(
            json.dumps(
                {
                    "serial": args.serial,
                    "device_id": claimed.json()["id"],
                    "board_type": claimed.json()["board_type"],
                    "status": "owned",
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
