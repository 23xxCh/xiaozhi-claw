"""Exercise control HTTP + gateway WSS + database Outbox using isolated mock audio."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, closing
from pathlib import Path

import httpx
import psutil
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

ROOT = Path(__file__).resolve().parents[1]
ADMIN_KEY = "dual-smoke-admin-key-for-local-tests"
SERIAL = "HENSUN-DUAL-SMOKE"
UTTERANCE = "今天有什么安排"


def _environment(directory: Path, gateway_port: int) -> dict[str, str]:
    # Keep only OS runtime requirements. Inherited application settings, proxy
    # variables and provider credentials must never influence this local probe.
    runtime_names = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in runtime_names}
    environment.update(
        {
            "PYTHONPATH": str(ROOT),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite+aiosqlite:///{(directory / 'smoke.db').as_posix()}",
            "ADMIN_API_KEY": ADMIN_KEY,
            "JWT_SECRET": "dual-smoke-jwt-secret-for-local-tests",
            "DEVICE_CREDENTIAL_PEPPER": "dual-smoke-device-pepper-for-local-tests",
            "MEMORY_MASTER_KEY": "dual-smoke-memory-key-for-local-tests",
            "EMAIL_OTP_SECRET": "dual-smoke-email-otp-for-local-tests",
            "PROVIDER_MODE": "mock",
            "FALLBACK_ENABLED": "false",
            "DOUBAO_REALTIME_ENABLED": "false",
            "WEB_SEARCH_MCP_ENABLED": "false",
            "WEB_SEARCH_QWEN_ENABLED": "false",
            "DEVICE_WS_URL": f"wss://127.0.0.1:{gateway_port}/v1/device/ws",
            "GATEWAY_ID": "dual-smoke-gateway",
            "COMMAND_POLL_INTERVAL_SECONDS": "0.1",
        }
    )
    return environment


def _free_ports() -> tuple[int, int]:
    # Reserve both at once so the two selections cannot return the same port.
    with socket.socket() as control, socket.socket() as gateway:
        control.bind(("127.0.0.1", 0))
        gateway.bind(("127.0.0.1", 0))
        return int(control.getsockname()[1]), int(gateway.getsockname()[1])


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _stop_owned_processes(processes: list[subprocess.Popen]) -> None:
    # Windows venv python.exe can be a launcher with a real Python child. Capture
    # descendants BEFORE stopping it, so no server is orphaned with open DB/logs.
    owned: dict[int, psutil.Process] = {}
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            parent = psutil.Process(process.pid)
            for child in [*parent.children(recursive=True), parent]:
                owned[child.pid] = child
        except psutil.NoSuchProcess:
            pass
    for child in owned.values():
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(list(owned.values()), timeout=5)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(alive, timeout=5)
    _require(not remaining, "an owned smoke subprocess did not exit")
    for process in processes:
        process.wait(timeout=5)


async def _wait_ready(url: str, process: subprocess.Popen, tls: ssl.SSLContext) -> None:
    async with httpx.AsyncClient(verify=tls, trust_env=False, timeout=1) as client:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            _require(process.poll() is None, "service exited before readiness")
            try:
                response = await client.get(f"{url}/health/ready")
                if response.status_code == 200 and response.json().get("status") == "ready":
                    return
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(0.1)
    raise TimeoutError("service did not become ready within 20 seconds")


async def _exercise(
    control_url: str, gateway_url: str, tls: ssl.SSLContext, database: Path
) -> dict[str, object]:
    async with httpx.AsyncClient(base_url=control_url, trust_env=False, timeout=5) as client:

        async def request(method: str, path: str, **kwargs):
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        registered = await request(
            "POST",
            "/v1/admin/devices",
            headers={"X-Admin-Key": ADMIN_KEY},
            json={"serial_number": SERIAL, "board_type": "hensun-nocam-pilot-v1"},
        )
        device_headers = {
            "Device-Id": SERIAL,
            "Authorization": f"Bearer {registered['device_secret']}",
        }
        bootstrap = await request(
            "POST",
            "/v1/device/bootstrap",
            headers=device_headers,
            json={"firmware_version": "2.4.2"},
        )
        login = await request(
            "POST",
            "/v1/auth/dev-login",
            json={"openid": "dual-smoke-owner", "adult_confirmed": True},
        )
        user_headers = {"Authorization": f"Bearer {login['access_token']}"}
        claimed = await request(
            "POST",
            "/v1/claims/confirm",
            headers=user_headers,
            json={"claim_code": bootstrap["claim_code"]},
        )
        connection = await request(
            "POST",
            "/v1/device/xiaozhi-bootstrap",
            headers=device_headers,
            json={"application": {"version": "2.4.2"}},
        )
        _require(
            connection["websocket"]["url"] == gateway_url, "bootstrap returned another gateway"
        )
        headers = {
            "Device-Id": SERIAL,
            "Authorization": f"Bearer {connection['websocket']['token']}",
            "Protocol-Version": "1",
        }

        async with connect(
            gateway_url,
            ssl=tls,
            additional_headers=headers,
            proxy=None,
            open_timeout=5,
            close_timeout=2,
        ) as websocket:

            async def send(payload: dict[str, object]) -> None:
                await websocket.send(json.dumps(payload))

            await send({"type": "hello", "version": 1, "features": {"strict_playback_ack": True}})
            hello = json.loads(await asyncio.wait_for(websocket.recv(), timeout=5))
            _require(hello.get("type") == "hello", "missing gateway hello")
            _require(
                hello.get("audio_params", {}).get("format") == "mock-utf8", "mock mode not active"
            )
            await send({"type": "listen", "state": "start", "mode": "auto"})
            await websocket.send(UTTERANCE.encode())
            await send({"type": "listen", "state": "stop"})
            audio: list[bytes] = []
            reply_id = turn_id = transcript = ""
            ready = drained = False
            async with asyncio.timeout(15):
                while True:
                    frame = await websocket.recv()
                    if isinstance(frame, bytes):
                        _require(
                            ready and not drained, "audio arrived outside acknowledged playback"
                        )
                        audio.append(frame)
                        continue
                    event = json.loads(frame)
                    kind, state = event.get("type"), event.get("state")
                    if kind == "error":
                        raise RuntimeError(f"mock turn failed: {event.get('code')}")
                    if kind == "stt":
                        transcript = event.get("text", "")
                    elif kind == "tts" and state == "start":
                        _require(not ready, "duplicate TTS start")
                        reply_id, turn_id = event["reply_id"], event["turn_id"]
                        await send(
                            {
                                "type": "tts",
                                "state": "ready",
                                "reply_id": reply_id,
                                "turn_id": turn_id,
                            }
                        )
                        ready = True
                    elif kind == "tts" and state == "stop":
                        _require(
                            bool(audio)
                            and event.get("reply_id") == reply_id
                            and event.get("turn_id") == turn_id,
                            "invalid playback stop",
                        )
                        await send(
                            {
                                "type": "tts",
                                "state": "drained",
                                "reply_id": reply_id,
                                "turn_id": turn_id,
                            }
                        )
                        drained = True
                    elif kind == "turn" and state == "completed":
                        _require(
                            ready
                            and drained
                            and event.get("reply_id") == reply_id
                            and event.get("turn_id") == turn_id,
                            "turn completed without matching acknowledgements",
                        )
                        break
            _require(transcript == UTTERANCE, "ASR did not preserve the mock input")
            _require(
                b"".join(audio).decode() == f"收到：{UTTERANCE}", "mock reply audio was incomplete"
            )
            entitlement = await request("GET", "/v1/account/entitlement", headers=user_headers)
            _require(
                entitlement["used_turns"] == 1, "control plane did not observe the completed turn"
            )
            devices = await request("GET", "/v1/devices", headers=user_headers)
            _require(
                any(item["id"] == claimed["id"] and item["online"] for item in devices),
                "control plane did not observe gateway online state",
            )
            unbound = await request(
                "POST", f"/v1/devices/{claimed['id']}/unbind", headers=user_headers
            )
            _require(unbound["lifecycle"] == "factory-unclaimed", "HTTP unbind did not complete")
            close_code = None
            async with asyncio.timeout(5):
                try:
                    while True:
                        await websocket.recv()
                except ConnectionClosed as closed:
                    close_code = closed.rcvd.code if closed.rcvd else None
            _require(close_code == 4403, "gateway did not revoke the old WSS connection")

        async with asyncio.timeout(5):
            while True:
                with closing(sqlite3.connect(database)) as db:
                    row = db.execute(
                        "SELECT status FROM device_commands WHERE device_id=? AND command_type=?",
                        (claimed["id"], "ownership-revoked"),
                    ).fetchone()
                if row == ("delivered",):
                    break
                await asyncio.sleep(0.1)
        _require(
            await request("GET", "/v1/devices", headers=user_headers) == [], "device remains owned"
        )
        return {
            "status": "passed",
            "transport": "wss",
            "audio_format": "mock-utf8",
            "completed_turns": 1,
            "ready_ack": ready,
            "drained_ack": drained,
            "gateway_close_code": close_code,
            "outbox_status": "delivered",
        }


def main() -> int:
    # Direct `python scripts/dual_process_smoke.py` and pytest imports share the
    # existing localhost certificate helper without loading application settings.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.wss_smoke import _write_ephemeral_certificate

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with (
        tempfile.TemporaryDirectory(prefix="hensun-dual-smoke-") as temporary,
        ExitStack() as stack,
    ):
        directory = Path(temporary)
        control_port, gateway_port = _free_ports()
        environment = _environment(directory, gateway_port)
        key, certificate = _write_ephemeral_certificate(directory)
        tls = ssl.create_default_context(cafile=str(certificate))
        control_url = f"http://127.0.0.1:{control_port}"
        gateway_https = f"https://127.0.0.1:{gateway_port}"
        processes: list[subprocess.Popen] = []
        logs: list[Path] = []
        try:
            # Prepare schema before either process can run create_all(). This
            # validates current metadata, not production migration compatibility.
            schema_log_path = directory / "schema.log"
            schema_log = stack.enter_context(schema_log_path.open("wb"))
            logs.append(schema_log_path)
            prepared = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import os; from sqlalchemy import create_engine; "
                    "from backend.app import models; from backend.app.db import Base; "
                    "engine=create_engine(os.environ['DATABASE_URL'].replace('+aiosqlite','')); "
                    "Base.metadata.create_all(engine); engine.dispose()",
                ],
                cwd=directory,
                env=environment,
                creationflags=flags,
                stdout=schema_log,
                stderr=subprocess.STDOUT,
            )
            processes.append(prepared)
            prepared.wait(timeout=30)
            _require(prepared.returncode == 0, "temporary schema preparation failed")
            for name, module, port, url in (
                ("control", "backend.app.main:app", control_port, control_url),
                ("gateway", "backend.realtime.main:app", gateway_port, gateway_https),
            ):
                command = [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    module,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                ]
                if name == "gateway":
                    command.extend(["--ssl-keyfile", str(key), "--ssl-certfile", str(certificate)])
                log_path = directory / f"{name}.log"
                log = stack.enter_context(log_path.open("wb"))
                logs.append(log_path)
                process = subprocess.Popen(
                    command,
                    cwd=directory,
                    env=environment,
                    creationflags=flags,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                processes.append(process)
                # Sequential readiness also prevents catalog insert races.
                asyncio.run(_wait_ready(url, process, tls))
            _require(processes[-2].pid != processes[-1].pid, "services share a process")
            result = asyncio.run(
                _exercise(control_url, environment["DEVICE_WS_URL"], tls, directory / "smoke.db")
            )
        except Exception as error:
            print(f"DUAL PROCESS SMOKE: FAIL: {error}", file=sys.stderr)
            for log_path in logs:
                print(
                    log_path.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr
                )
            return 1
        finally:
            # Only these exact Popen children and this TemporaryDirectory belong
            # to the probe. Never kill listeners discovered elsewhere on the host.
            _stop_owned_processes(processes)
    # A PASS includes successful child-process and temporary-directory cleanup.
    print(json.dumps({**result, "processes": 2, "schema": "metadata-create-all"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
