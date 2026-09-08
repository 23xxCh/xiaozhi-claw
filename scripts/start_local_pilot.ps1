param(
    [string]$HostAddress = "",
    [string]$ProviderHostOverrides = "",
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

function Set-EnvFileValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    $lines = Get-Content -LiteralPath $Path
    $replacement = "$Key=$Value"
    $found = $false
    $updated = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Key))=") {
            $found = $true
            $replacement
        }
        else {
            $line
        }
    }
    if (-not $found) {
        $updated += $replacement
    }
    Set-Content -LiteralPath $Path -Value $updated -Encoding utf8
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
if ([string]::IsNullOrWhiteSpace($HostAddress)) {
    $addresses = @(Get-NetIPConfiguration | Where-Object {
        $_.NetAdapter.HardwareInterface -and $_.NetAdapter.Status -eq "Up" -and $_.IPv4DefaultGateway
    } | ForEach-Object { $_.IPv4Address.IPAddress } | Select-Object -Unique)
    if ($addresses.Count -ne 1) {
        throw "Cannot choose one active LAN address. Pass -HostAddress explicitly."
    }
    $HostAddress = $addresses[0]
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
        if ($existing.host_address -ne $HostAddress -or $existing.web_api_mode -ne "same-origin" -or $ProviderHostOverrides) {
            throw "Running services use an earlier configuration. Run scripts/stop_local_pilot.ps1, then start again. No settings were changed."
        }
        Write-Output "Hensun local pilot is already running."
        if (-not $NoBrowser) {
            Start-Process "http://$HostAddress`:3000"
        }
        return
    }
    if (
        (Test-TrackedProcess -ProcessId $existing.control_pid -Marker "backend.app.main:app") -or
        (Test-TrackedProcess -ProcessId $existing.gateway_pid -Marker "backend.realtime.main:app") -or
        (Test-TrackedProcess -ProcessId $existing.web_pid -Marker "next/dist/bin/next")
    ) {
        throw "Tracked pilot processes are still running but not healthy. Run scripts/stop_local_pilot.ps1 before starting again. No settings were changed."
    }
}

foreach ($port in 3000, 8000, 8001) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listener) {
        throw "Port $port is already occupied by PID $($listener[0].OwningProcess)."
    }
}
if (Test-Path -LiteralPath $statePath) {
    Remove-Item -LiteralPath $statePath -Force
}

Push-Location $projectRoot
try {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $databasePath = Join-Path $projectRoot "hensun-lan.db"
    $databaseBackup = $null
    $backupRoot = Join-Path $projectRoot "run\backups\$timestamp"
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env") -Destination (Join-Path $backupRoot "backend.env.before")
    Copy-Item -LiteralPath (Join-Path $projectRoot "web\.env.local") -Destination (Join-Path $backupRoot "web.env.local.before")
    if (Test-Path -LiteralPath $databasePath) {
        $databaseBackup = Join-Path $backupRoot "hensun-lan.db"
        Copy-Item -LiteralPath $databasePath -Destination $databaseBackup
    }

    Set-EnvFileValue -Path (Join-Path $projectRoot ".env") `
        -Key "DEVICE_WS_URL" -Value "ws://$HostAddress`:8001/v1/device/ws"
    Set-EnvFileValue -Path (Join-Path $projectRoot ".env") `
        -Key "WEB_APP_URL" -Value "http://$HostAddress`:3000"
    $originsLine = Get-Content -LiteralPath (Join-Path $projectRoot ".env") |
        Where-Object { $_ -match "^CORS_ORIGINS=" } | Select-Object -Last 1
    $origins = @("http://127.0.0.1:3000", "http://localhost:3000", "http://$HostAddress`:3000")
    if ($originsLine) { $origins += ($originsLine -replace "^CORS_ORIGINS=", "").Split(",") }
    $origins = $origins | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -Unique
    Set-EnvFileValue -Path (Join-Path $projectRoot ".env") `
        -Key "CORS_ORIGINS" -Value ($origins -join ",")
    Set-EnvFileValue -Path (Join-Path $projectRoot "web\.env.local") `
        -Key "NEXT_PUBLIC_CONTROL_API_URL" -Value "/"
    Set-EnvFileValue -Path (Join-Path $projectRoot "web\.env.local") `
        -Key "CONTROL_API_PROXY_URL" -Value "http://127.0.0.1:8000"
    if (-not [string]::IsNullOrWhiteSpace($ProviderHostOverrides)) {
        Set-EnvFileValue -Path (Join-Path $projectRoot ".env") `
            -Key "PROVIDER_HOST_OVERRIDES" -Value $ProviderHostOverrides
    }

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
        web_api_mode = "same-origin"
        database_backup = $databaseBackup
        control_log = $controlOut
        gateway_log = $gatewayOut
        web_log = $webOut
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
        web_api_mode = "same-origin"
        database_backup = $databaseBackup
        control_log = $controlOut
        gateway_log = $gatewayOut
        web_log = $webOut
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

    Write-Output "Hensun local pilot is ready: http://$HostAddress`:3000"
    Write-Output "Control API: http://$HostAddress`:8000/docs"
    Write-Output "Realtime gateway: ws://$HostAddress`:8001/v1/device/ws"
    if ($databaseBackup) {
        Write-Output "Database backup: $databaseBackup"
    }
    if (-not $NoBrowser) {
        Start-Process "http://$HostAddress`:3000"
    }
}
finally {
    Pop-Location
}
