$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot "run\local-pilot\processes.json"

if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Output "Hensun local pilot is not running."
    return
}

$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
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

Remove-Item -LiteralPath $statePath -Force
Write-Output "Hensun local pilot stopped."
