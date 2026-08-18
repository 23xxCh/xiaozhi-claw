param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("official", "selfhosted", "local")]
    [string]$Variant,

    [string]$Port = "",
    [string]$BootstrapUrl = "",
    [ValidateRange(115200, 921600)]
    [int]$Baud = 460800
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$firmwareRoot = Join-Path $projectRoot "firmware\xiaozhi-esp32"

function Resolve-Ch340Port {
    param([string]$RequestedPort)

    $devices = @(
        Get-CimInstance Win32_PnPEntity -ErrorAction Stop |
            Where-Object {
                $_.Name -match '\(COM\d+\)' -and (
                    $_.Name -match 'CH340|CH341' -or
                    $_.PNPDeviceID -match 'VID_1A86'
                )
            } |
            ForEach-Object {
                if ($_.Name -match '\((COM\d+)\)') {
                    $Matches[1].ToUpperInvariant()
                }
            } |
            Sort-Object -Unique
    )
    if ($RequestedPort) {
        $normalized = $RequestedPort.ToUpperInvariant()
        if ($normalized -notmatch '^COM\d+$') {
            throw "Invalid serial port: $RequestedPort"
        }
        if ($normalized -notin $devices) {
            throw "Requested port $normalized is not an online USB-SERIAL CH340/CH341 device. Detected: $($devices -join ', ')"
        }
        return $normalized
    }
    if ($devices.Count -ne 1) {
        throw "Expected exactly one online USB-SERIAL CH340/CH341 device, detected $($devices.Count): $($devices -join ', ')"
    }
    return $devices[0]
}

$buildArgs = @{ Variant = $Variant }
if ($Variant -ne "official") {
    $buildArgs.BootstrapUrl = $BootstrapUrl
}
$result = & (Join-Path $PSScriptRoot "build_firmware.ps1") @buildArgs

$buildDirectory = Join-Path $firmwareRoot "build"
$planner = Join-Path $PSScriptRoot "safe_flash_plan.py"
$helperPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $helperPython)) {
    $helperPython = (Get-Command python -ErrorAction Stop).Source
}
$planArgs = @($planner, "--build-dir", $buildDirectory)
if ($Variant -ne "official") {
    $planArgs += "--require-emote"
}
$planJson = & $helperPython @planArgs
if ($LASTEXITCODE -ne 0 -or -not $planJson) {
    throw "Could not resolve a preserve-identity flash plan."
}
$plan = $planJson | ConvertFrom-Json
foreach ($segment in $plan.segments) {
    if ($segment.name -notin @("app", "emote_gen")) {
        throw "Unsafe flash segment rejected: $($segment.name)"
    }
}

# Resolve the physical port immediately before writing. Never retain a historical
# COM number or fall through to ESP-IDF's full-project flash target.
$resolvedPort = Resolve-Ch340Port -RequestedPort $Port
$idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $idfPython)) {
    throw "The active ESP-IDF Python environment was not found: $idfPython"
}

$esptoolArgs = @(
    "-m", "esptool",
    "--chip", $plan.chip,
    "--port", $resolvedPort,
    "--baud", "$Baud",
    "--before", $plan.before,
    "--after", $plan.after,
    "write_flash",
    "--flash_mode", $plan.flash_mode,
    "--flash_freq", $plan.flash_freq,
    "--flash_size", $plan.flash_size
)
foreach ($segment in $plan.segments) {
    $esptoolArgs += @($segment.offset, $segment.path)
    Write-Host "Safe flash segment: $($segment.name) $($segment.offset) $($segment.path)"
}

& $idfPython @esptoolArgs
if ($LASTEXITCODE -ne 0) {
    throw "Preserve-identity flash of $($result.FirmwareName) to $resolvedPort failed."
}

$result | Add-Member -NotePropertyName Port -NotePropertyValue $resolvedPort -PassThru
