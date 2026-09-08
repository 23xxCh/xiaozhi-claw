"""Run browser checks against an isolated production build and mock backend."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

import httpx
from dual_process_smoke import ROOT, _environment, _free_ports, _stop_owned_processes


def main() -> int:
    node = shutil.which("node")
    modules = ROOT / "web/node_modules"
    if not node or not modules.is_dir():
        raise RuntimeError("Install Node.js and web dependencies before running this check")
    (ROOT / "run").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="web-e2e-", dir=ROOT / "run"))
    web = directory / "web"
    web.mkdir()
    for name in ("app", "components", "lib", "public", "tests"):
        shutil.copytree(ROOT / "web" / name, web / name)
    for name in ("package.json", "tsconfig.json", "next-env.d.ts",
                 "next.config.ts", "playwright.config.ts"):
        shutil.copy2(ROOT / "web" / name, web / name)
    control_port, web_port = _free_ports()
    env = _environment(directory, 0)
    env.update({
        "ALIYUN_DIALOG_ENABLED": "false",
        "SESSION_COOKIE_SECURE": "false",
        "EMAIL_DELIVERY_MODE": "development",
        "WEB_APP_URL": f"http://127.0.0.1:{web_port}",
        "CORS_ORIGINS": f"http://127.0.0.1:{web_port}",
        "NEXT_PUBLIC_CONTROL_API_URL": "/",
        "CONTROL_API_PROXY_URL": f"http://127.0.0.1:{control_port}",
        "PLAYWRIGHT_BASE_URL": f"http://127.0.0.1:{web_port}",
        "E2E_CONTROL_API_URL": f"http://127.0.0.1:{control_port}",
        "E2E_DEV_OPENID": "isolated-web-tester",
        "PLAYWRIGHT_START_WEB": "false",
        "HENSUN_ISOLATED_WEB_E2E": "1",
    })
    if os.name == "nt":
        subprocess.run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
            "-Path $env:HENSUN_E2E_LINK -Target $env:HENSUN_E2E_MODULES | Out-Null",
        ], env={**env, "HENSUN_E2E_LINK": str(web / "node_modules"),
                "HENSUN_E2E_MODULES": str(modules)}, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        (web / "node_modules").symlink_to(modules, target_is_directory=True)

    print(f"Isolated artifacts: {directory}", flush=True)
    processes: list[subprocess.Popen] = []
    next_cli = str(modules / "next/dist/bin/next")
    with ExitStack() as stack:
        def start(args: list[str], cwd: Path, log_name: str) -> subprocess.Popen:
            log = stack.enter_context((directory / log_name).open("w", encoding="utf-8"))
            process = subprocess.Popen(
                args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            processes.append(process)
            return process

        try:
            build = start([node, next_cli, "build", "--webpack"], web, "build.log")
            if build.wait(timeout=300):
                raise RuntimeError("Production build failed; inspect build.log")
            servers = [
                start([sys.executable, "-m", "uvicorn", "backend.app.main:app",
                       "--host", "127.0.0.1", "--port", str(control_port)],
                      directory, "backend.log"),
                start([node, next_cli, "start", "-p", str(web_port),
                       "--hostname", "127.0.0.1"], web, "web.log"),
            ]
            for port, path in ((control_port, "/health/ready"), (web_port, "/login")):
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    if any(server.poll() is not None for server in servers):
                        raise RuntimeError("Isolated server exited; inspect server logs")
                    try:
                        if httpx.get(f"http://127.0.0.1:{port}{path}", timeout=2).is_success:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError("Isolated server startup timed out")
            result = start([node, str(modules / "@playwright/test/cli.js"), "test",
                            "--workers=1", "--max-failures=5", "--reporter=json"],
                           web, "playwright-report.json")
            code = result.wait(timeout=600)
        finally:
            _stop_owned_processes(processes)
    report = json.loads((directory / "playwright-report.json").read_text(encoding="utf-8"))
    print(json.dumps(report["stats"], ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
