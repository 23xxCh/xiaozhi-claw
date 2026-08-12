"""Start the control plane and realtime gateway as separate processes and probe health."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def _json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=2) as response:  # noqa: S310 - fixed localhost URL
        return json.load(response)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hensun-dual-smoke-") as temp_dir:
        database = Path(temp_dir, "smoke.db").as_posix()
        environment = os.environ.copy()
        environment.update(
            {
                "APP_ENV": "test",
                "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
                "PROVIDER_MODE": "mock",
                "DEVICE_WS_URL": "ws://127.0.0.1:8111/v1/device/ws",
            }
        )
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        commands = (
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8110",
            ],
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.realtime.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8111",
            ],
        )
        processes = [
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                creationflags=flags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            for command in commands
        ]
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if any(process.poll() is not None for process in processes):
                    errors = "\n".join(
                        process.stderr.read() if process.stderr else ""
                        for process in processes
                    )
                    raise RuntimeError(f"a service exited before readiness:\n{errors}")
                try:
                    control = _json("http://127.0.0.1:8110/health/live")
                    gateway = _json("http://127.0.0.1:8111/health/live")
                    gateway_openapi = _json("http://127.0.0.1:8111/openapi.json")
                    if control.get("status") == gateway.get("status") == "ok":
                        print(
                            "dual process smoke passed:",
                            gateway_openapi["info"]["title"],  # type: ignore[index]
                        )
                        return 0
                except (OSError, KeyError, json.JSONDecodeError):
                    time.sleep(0.25)
            raise TimeoutError("services did not become healthy within 15 seconds")
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
