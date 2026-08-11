from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import websockets
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
ADMIN_KEY = "wss-smoke-admin-key"
SERIAL = "02:00:00:00:00:01"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _write_ephemeral_certificate(directory: Path) -> tuple[Path, Path]:
    """Create a localhost-only certificate for this development smoke test."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(minutes=10))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = directory / "wss-smoke-key.pem"
    cert_path = directory / "wss-smoke-cert.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


async def _wait_for_server(base_url: str, process: subprocess.Popen[str]) -> None:
    async with httpx.AsyncClient(verify=False) as client:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Uvicorn exited before the health check passed")
            try:
                response = await client.get(f"{base_url}/health/live", timeout=0.5)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError("Timed out waiting for the local WSS server")


async def _provision_device(client: httpx.AsyncClient) -> str:
    registered = await client.post(
        "/v1/admin/devices",
        headers={"X-Admin-Key": ADMIN_KEY},
        json={"serial_number": SERIAL, "board_type": "hensun-cam-pilot-v1"},
    )
    registered.raise_for_status()
    device_secret = registered.json()["device_secret"]

    bootstrapped = await client.post(
        "/v1/device/bootstrap",
        headers={"Device-Id": SERIAL, "Authorization": f"Bearer {device_secret}"},
        json={"firmware_version": "2.4.2"},
    )
    bootstrapped.raise_for_status()
    logged_in = await client.post(
        "/v1/auth/dev-login",
        json={"openid": "wss-smoke-user", "adult_confirmed": True},
    )
    logged_in.raise_for_status()
    claimed = await client.post(
        "/v1/claims/confirm-phone",
        headers={"Authorization": f"Bearer {logged_in.json()['access_token']}"},
        json={"claim_code": bootstrapped.json()["claim_code"]},
    )
    claimed.raise_for_status()
    compatibility_bootstrap = await client.post(
        "/v1/device/xiaozhi-bootstrap",
        headers={"Device-Id": SERIAL, "Client-Id": "wss-smoke"},
        json={"application": {"version": "2.4.2-local"}},
    )
    compatibility_bootstrap.raise_for_status()
    return str(compatibility_bootstrap.json()["websocket"]["token"])


async def _exercise_wss(base_url: str, port: int) -> dict[str, object]:
    async with httpx.AsyncClient(base_url=base_url, verify=False) as client:
        device_token = await _provision_device(client)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.check_hostname = False
        tls.verify_mode = ssl.CERT_NONE
        headers = {
            "Device-Id": SERIAL,
            "Authorization": f"Bearer {device_token}",
            "Protocol-Version": "1",
        }
        async with websockets.connect(
            f"wss://127.0.0.1:{port}/v1/device/ws",
            ssl=tls,
            additional_headers=headers,
            proxy=None,
        ) as websocket:
            await websocket.send(json.dumps({"type": "hello", "version": 1}))
            hello = json.loads(await websocket.recv())
            if hello.get("type") != "hello" or hello.get("transport") != "websocket":
                raise RuntimeError(f"Unexpected server hello: {hello}")

            delivery = await client.post(
                f"/v1/admin/device-events/{SERIAL}",
                headers={"X-Admin-Key": ADMIN_KEY},
                json={
                    "event": "reminder",
                    "message_type": "alert",
                    "status": "WSS 联调",
                    "message": "自有服务事件已到达设备连接",
                },
            )
            delivery.raise_for_status()
            event = json.loads(await websocket.recv())
            if event.get("emotion") != "reminder":
                raise RuntimeError(f"Unexpected device event: {event}")
            return {"transport": "wss", "hello": hello, "delivery": delivery.json(), "event": event}


def main() -> int:
    port = _free_port()
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(
        prefix="hensun-wss-smoke-", ignore_cleanup_errors=True
    ) as temporary:
        temp_dir = Path(temporary)
        key_path, cert_path = _write_ephemeral_certificate(temp_dir)
        environment = os.environ.copy()
        environment.update(
            {
                "APP_ENV": "test",
                "DATABASE_URL": f"sqlite+aiosqlite:///{(temp_dir / 'smoke.db').as_posix()}",
                "ADMIN_API_KEY": ADMIN_KEY,
                "JWT_SECRET": "wss-smoke-jwt-secret-with-enough-entropy",
                "DEVICE_CREDENTIAL_PEPPER": "wss-smoke-device-pepper-with-enough-entropy",
                "MEMORY_MASTER_KEY": "wss-smoke-memory-key-with-enough-entropy",
                "PROVIDER_MODE": "mock",
                "DEVICE_WS_URL": f"wss://127.0.0.1:{port}/v1/device/ws",
            }
        )
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ssl-keyfile",
            str(key_path),
            "--ssl-certfile",
            str(cert_path),
            "--log-level",
            "warning",
        ]
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creation_flags,
        )
        try:
            base_url = f"https://127.0.0.1:{port}"
            asyncio.run(_wait_for_server(base_url, process))
            result = asyncio.run(_exercise_wss(base_url, port))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print("WSS SMOKE: PASS (ephemeral self-signed certificate; development only)")
            return 0
        except Exception as error:
            print(f"WSS SMOKE: FAIL: {error}", file=sys.stderr)
            return 1
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            time.sleep(0.2)
            if process.returncode not in {0, 1, -15} and process.stdout:
                print(process.stdout.read(), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
