param(
    [string]$HostAddress = "192.168.5.49",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot "run\local-pilot"
$statePath = Join-Path $runtimeRoot "processes.json"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$node = (Get-Command node.exe -ErrorAction Stop).Source

function Test-Endpoint {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Test-TrackedProcess {
    param([int]$ProcessId, [string]$Marker)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    return $null -ne $process -and $process.CommandLine -like "*$Marker*"
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment is missing: $python"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
    throw "Backend .env is missing."
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "web\.env.local"))) {
    throw "Frontend web/.env.local is missing."
}
if (-not (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object IPAddress -eq $HostAddress)) {
    throw "This computer does not currently own LAN address $HostAddress."
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

if (Test-Path -LiteralPath $statePath) {
    $existing = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    $healthy = (
        (Test-TrackedProcess -ProcessId $existing.control_pid -Marker "backend.app.main:app") -and
        (Test-TrackedProcess -ProcessId $existing.gateway_pid -Marker "backend.realtime.main:app") -and
        (Test-TrackedProcess -ProcessId $existing.web_pid -Marker "next/dist/bin/next") -and
        (Test-Endpoint -Url "http://127.0.0.1:8000/health/ready") -and
        (Test-Endpoint -Url "http://127.0.0.1:8001/health/ready") -and
        (Test-Endpoint -Url "http://127.0.0.1:3000")
    )
    if ($healthy) {
        Write-Output "Hensun local pilot is already running."
        if (-not $NoBrowser) {
            Start-Process "http://$HostAddress`:3000"
        }
        return
    }
    Remove-Item -LiteralPath $statePath -Force
}

foreach ($port in 3000, 8000, 8001) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listener) {
        throw "Port $port is already occupied by PID $($listener[0].OwningProcess)."
    }
}

Push-Location $projectRoot
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Database migration failed."
    }
    Push-Location (Join-Path $projectRoot "web")
    try {
        & $npm run build
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend production build failed."
        }
    }
    finally {
        Pop-Location
    }

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $controlOut = Join-Path $runtimeRoot "control-$timestamp.log"
    $gatewayOut = Join-Path $runtimeRoot "gateway-$timestamp.log"
    $webOut = Join-Path $runtimeRoot "web-$timestamp.log"

    $control = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000") `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput $controlOut -RedirectStandardError "$controlOut.err" -PassThru
    $gateway = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "backend.realtime.main:app", "--host", "0.0.0.0", "--port", "8001") `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput $gatewayOut -RedirectStandardError "$gatewayOut.err" -PassThru
    $web = Start-Process -FilePath $node `
        -ArgumentList @("node_modules/next/dist/bin/next", "start", "--hostname", "0.0.0.0", "--port", "3000") `
        -WorkingDirectory (Join-Path $projectRoot "web") -WindowStyle Hidden `
        -RedirectStandardOutput $webOut -RedirectStandardError "$webOut.err" -PassThru

    [ordered]@{
        control_pid = $control.Id
        gateway_pid = $gateway.Id
        web_pid = $web.Id
        started_at = (Get-Date).ToString("o")
        host_address = $HostAddress
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

    $deadline = (Get-Date).AddSeconds(30)
    do {
        $ready = (
            (Test-Endpoint -Url "http://127.0.0.1:8000/health/ready") -and
            (Test-Endpoint -Url "http://127.0.0.1:8001/health/ready") -and
            (Test-Endpoint -Url "http://127.0.0.1:3000")
        )
        if (-not $ready) {
            Start-Sleep -Milliseconds 500
        }
    } until ($ready -or (Get-Date) -ge $deadline)

    if (-not $ready) {
        & (Join-Path $PSScriptRoot "stop_local_pilot.ps1")
        throw "Local pilot services did not become healthy within 30 seconds."
    }

    $controlListener = Get-NetTCPConnection -State Listen -LocalPort 8000 |
        Select-Object -First 1
    $gatewayListener = Get-NetTCPConnection -State Listen -LocalPort 8001 |
        Select-Object -First 1
    $webListener = Get-NetTCPConnection -State Listen -LocalPort 3000 |
        Select-Object -First 1
    [ordered]@{
        control_pid = $controlListener.OwningProcess
        control_launcher_pid = $control.Id
        gateway_pid = $gatewayListener.OwningProcess
        gateway_launcher_pid = $gateway.Id
        web_pid = $webListener.OwningProcess
        started_at = (Get-Date).ToString("o")
        host_address = $HostAddress
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

    Write-Output "Hensun local pilot is ready: http://$HostAddress`:3000"
    Write-Output "Control API: http://$HostAddress`:8000/docs"
    Write-Output "Realtime gateway: ws://$HostAddress`:8001/v1/device/ws"
    if (-not $NoBrowser) {
        Start-Process "http://$HostAddress`:3000"
    }
}
finally {
    Pop-Location
}
