$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$stdoutPath = Join-Path $projectRoot "lan-backend.stdout.log"
$stderrPath = Join-Path $projectRoot "lan-backend.stderr.log"

$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    throw "Port 8000 is already in use by PID $($listener.OwningProcess)."
}

$env:APP_ENV = "development"
$env:DATABASE_URL = "sqlite+aiosqlite:///./hensun-lan.db"
$env:DEVICE_WS_URL = "ws://192.168.5.49:8000/v1/device/ws"
$env:ADMIN_API_KEY = "development-admin-change-me"

$process = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList @(
        "-m", "uvicorn", "backend.app.main:app",
        "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"
    ) `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

$ready = $false
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/ready" -TimeoutSec 1
        if ($health.status -eq "ready") {
            $ready = $true
            break
        }
    }
    catch {
        Start-Sleep -Milliseconds 200
    }
}

if (-not $ready) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "LAN backend did not become ready. See $stderrPath"
}

[pscustomobject]@{
    Pid = $process.Id
    LocalUrl = "http://127.0.0.1:8000"
    LanUrl = "http://192.168.5.49:8000"
    Status = $health.status
}
