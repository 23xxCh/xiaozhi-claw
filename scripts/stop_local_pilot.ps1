$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot "run\local-pilot\processes.json"
$reportRoot = Join-Path $projectRoot "run\reports"

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

function Get-ErrorCount {
    param([string]$LogPath)
    if (-not $LogPath -or -not (Test-Path -LiteralPath "$LogPath.err")) {
        return 0
    }
    return @(
        Select-String -LiteralPath "$LogPath.err" -Pattern "ERROR|Traceback|Exception" `
            -ErrorAction SilentlyContinue
    ).Count
}

if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Output "Hensun local pilot is not running."
    return
}

$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$endedAt = Get-Date
$startedAt = [DateTimeOffset]::Parse([string]$state.started_at)
$healthBeforeStop = [ordered]@{
    control = Test-Endpoint "http://127.0.0.1:8000/health/ready"
    gateway = Test-Endpoint "http://127.0.0.1:8001/health/ready"
    web = Test-Endpoint "http://127.0.0.1:3000"
}
$targets = @(
    @{ pid = [int]$state.control_pid; marker = "backend.app.main:app" },
    @{ pid = [int]$state.gateway_pid; marker = "backend.realtime.main:app" },
    @{ pid = [int]$state.web_pid; marker = "next/dist/bin/next" }
)
if ($null -ne $state.control_launcher_pid) {
    $targets += @{ pid = [int]$state.control_launcher_pid; marker = "backend.app.main:app" }
}
if ($null -ne $state.gateway_launcher_pid) {
    $targets += @{ pid = [int]$state.gateway_launcher_pid; marker = "backend.realtime.main:app" }
}

foreach ($target in $targets | Sort-Object pid -Unique) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($target.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        continue
    }
    if ($process.CommandLine -notlike "*$($target.marker)*") {
        throw "Refusing to stop PID $($target.pid): command does not match Hensun service."
    }
    Stop-Process -Id $target.pid
    Wait-Process -Id $target.pid -Timeout 10 -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
$reportTimestamp = $endedAt.ToString("yyyyMMdd-HHmmss")
$reportPath = Join-Path $reportRoot "local-pilot-$reportTimestamp.json"
$report = [ordered]@{
    started_at = $startedAt.ToString("o")
    ended_at = $endedAt.ToString("o")
    duration_seconds = [Math]::Round(($endedAt - $startedAt.LocalDateTime).TotalSeconds, 1)
    host_address = [string]$state.host_address
    health_before_stop = $healthBeforeStop
    error_counts = [ordered]@{
        control = Get-ErrorCount ([string]$state.control_log)
        gateway = Get-ErrorCount ([string]$state.gateway_log)
        web = Get-ErrorCount ([string]$state.web_log)
    }
    database_backup = [string]$state.database_backup
}
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportPath -Encoding utf8
Remove-Item -LiteralPath $statePath -Force
Write-Output "Hensun local pilot stopped."
Write-Output "Runtime report: $reportPath"
