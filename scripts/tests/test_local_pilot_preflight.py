"""A running or conflicting pilot must never have its settings silently rewritten."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

POWERSHELL = shutil.which("pwsh")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not POWERSHELL or os.name != "nt", reason="Windows pilot launcher")
@pytest.mark.parametrize(
    "case", ["running", "changed-host", "legacy-mode", "starting", "occupied", "ambiguous-lan"]
)
def test_pilot_preflight_preserves_running_configuration(tmp_path: Path, case: str) -> None:
    (tmp_path / "scripts").mkdir()
    launcher = (ROOT / "scripts/start_local_pilot.ps1").read_text(encoding="utf-8-sig")
    launcher = launcher.replace(
        '$python = Join-Path $projectRoot ".venv\\Scripts\\python.exe"',
        "$python = '" + sys.executable.replace("'", "''") + "'",
    )
    (tmp_path / "scripts/start_local_pilot.ps1").write_text(launcher, encoding="utf-8")
    (tmp_path / "scripts/local_pilot_supervisor.py").write_text(
        "import sys\nassert sys.argv[1:] == ['--ensure']\n", encoding="utf-8",
    )
    (tmp_path / ".venv/Scripts").mkdir(parents=True)
    (tmp_path / ".venv/Scripts/python.exe").touch()
    (tmp_path / "web").mkdir()
    (tmp_path / ".env").write_text("PRESERVE_BACKEND=example\n")
    (tmp_path / "web/.env.local").write_text("PRESERVE_WEB=example\n")
    state = tmp_path / "run/local-pilot/processes.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "control_pid": 1, "gateway_pid": 2, "web_pid": 3,
        "host_address": "192.0.2.5" if case == "changed-host" else "192.0.2.10",
        "web_api_mode": "legacy" if case == "legacy-mode" else "same-origin",
    }))
    protected = [tmp_path / ".env", tmp_path / "web/.env.local", state]
    before = [p.read_bytes() for p in protected]
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", r'''
function Get-Command { [pscustomobject]@{Source="not-invoked.exe"} }
function Get-NetIPConfiguration {
    [pscustomobject]@{NetAdapter=[pscustomobject]@{HardwareInterface=$true;Status="Up"};IPv4DefaultGateway="gateway";IPv4Address=[pscustomobject]@{IPAddress="192.0.2.10"}}
    if ($env:PILOT_TEST_CASE -eq "ambiguous-lan") {
        [pscustomobject]@{NetAdapter=[pscustomobject]@{HardwareInterface=$true;Status="Up"};IPv4DefaultGateway="gateway";IPv4Address=[pscustomobject]@{IPAddress="192.0.2.11"}}
    }
}
function Get-NetIPAddress { [pscustomobject]@{IPAddress="192.0.2.10"} }
function Get-CimInstance {
    if ($env:PILOT_TEST_CASE -ne "occupied") {
        [pscustomobject]@{
            CommandLine="backend.app.main:app backend.realtime.main:app next/dist/bin/next"
        }
    }
}
function Get-NetTCPConnection {
    if ($env:PILOT_TEST_CASE -ne "starting") { [pscustomobject]@{OwningProcess=999} }
}
function Invoke-WebRequest {
    [pscustomobject]@{StatusCode=$(if ($env:PILOT_TEST_CASE -eq "starting") { 503 } else { 200 })}
}
try { & (Join-Path $env:PILOT_TEST_ROOT "scripts/start_local_pilot.ps1") -NoBrowser; exit 0 }
catch { Write-Output $_.Exception.Message; exit 2 }
'''],
        env={**os.environ, "PILOT_TEST_ROOT": str(tmp_path), "PILOT_TEST_CASE": case},
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == (0 if case == "running" else 2), result.stdout + result.stderr
    assert [p.read_bytes() for p in protected] == before
    expected = {
        "running": "already running", "changed-host": "No settings were changed",
        "legacy-mode": "No settings were changed", "occupied": "already occupied",
        "starting": "still running but not healthy",
        "ambiguous-lan": "Pass -HostAddress explicitly",
    }
    assert expected[case] in result.stdout
