param(
    [Parameter(Mandatory = $true)]
    [string]$ReleaseManifestPath,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9:._-]{2,63}$')]
    [string]$SerialNumber,

    [Parameter(Mandatory = $true)]
    [SecureString]$DeviceSecret,

    [string]$Port = "",
    [ValidateRange(115200, 921600)]
    [int]$Baud = 460800,

    [Parameter(Mandatory = $true)]
    [switch]$ConfirmFactoryReset
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

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
        if ($normalized -notmatch '^COM\d+$' -or $normalized -notin $devices) {
            throw "Requested port $RequestedPort is not an online CH340/CH341 device. Detected: $($devices -join ', ')"
        }
        return $normalized
    }
    if ($devices.Count -ne 1) {
        throw "Expected exactly one online CH340/CH341 device, detected $($devices.Count): $($devices -join ', ')"
    }
    return $devices[0]
}

function Resolve-ReleaseImage {
    param(
        [string]$ReleaseRoot,
        [object]$Segment
    )

    $candidate = [IO.Path]::GetFullPath((Join-Path $ReleaseRoot $Segment.file))
    $rootPrefix = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Release segment path escapes the release directory: $($Segment.name)"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Release segment is missing: $candidate"
    }
    $bytes = (Get-Item -LiteralPath $candidate).Length
    if ($bytes -ne [int64]$Segment.bytes -or $bytes -gt [int64]$Segment.maximum_size) {
        throw "Release segment size is invalid: $($Segment.name)"
    }
    $actualHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
    if ($actualHash -ne ([string]$Segment.sha256).ToUpperInvariant()) {
        throw "Release segment SHA-256 does not match: $($Segment.name)"
    }
    return $candidate
}

if (-not $ConfirmFactoryReset) {
    throw "Factory flashing requires -ConfirmFactoryReset because it clears saved Wi-Fi."
}

$manifestItem = Get-Item -LiteralPath $ReleaseManifestPath -ErrorAction Stop
$releaseRoot = $manifestItem.Directory.FullName
$release = Get-Content -LiteralPath $manifestItem.FullName -Raw -Encoding utf8 | ConvertFrom-Json
if ($release.schema_version -ne 1 -or $release.profile_id -ne "hensun-nocam-pilot-v1") {
    throw "Only the reviewed hensun-nocam-pilot-v1 release is accepted by this station."
}
if ($release.board_type -ne "hensun-nocam-pilot-v1" -or
    $release.chip -ne "esp32s3" -or $release.flash_size -ne "16MB") {
    throw "The release target does not match the non-CAM ESP32-S3 N16R8 profile."
}

$expectedSegments = @("bootloader", "partition-table", "otadata", "app", "assets")
$actualSegments = @($release.segments | ForEach-Object { $_.name })
if ((Compare-Object $expectedSegments $actualSegments).Count -ne 0) {
    throw "Release manifest contains an unexpected partition set."
}
$resolvedSegments = @(
    foreach ($segment in $release.segments) {
        [pscustomobject]@{
            name = $segment.name
            offset = $segment.offset
            path = Resolve-ReleaseImage -ReleaseRoot $releaseRoot -Segment $segment
            bytes = [int64]$segment.bytes
            sha256 = ([string]$segment.sha256).ToLowerInvariant()
        }
    }
)

$resolvedPort = Resolve-Ch340Port -RequestedPort $Port
$idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $idfPython)) {
    throw "The active ESP-IDF Python environment was not found: $idfPython"
}

$chipProbe = (& $idfPython -m esptool --chip esp32s3 --port $resolvedPort chip-id 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $chipProbe -notmatch 'ESP32-S3') {
    throw "The connected device is not an ESP32-S3."
}
$flashProbe = (& $idfPython -m esptool --chip esp32s3 --port $resolvedPort flash-id 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $flashProbe -notmatch 'Detected flash size:\s*16MB') {
    throw "The connected device does not report 16MB flash."
}
$macMatch = [regex]::Match($chipProbe + $flashProbe, 'MAC:\s*([0-9A-Fa-f:]{17})')
if (-not $macMatch.Success) {
    throw "Could not read the device MAC before flashing."
}
$deviceMac = $macMatch.Groups[1].Value.ToUpperInvariant()
if ($SerialNumber -match '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$' -and
    $SerialNumber.ToUpperInvariant() -ne $deviceMac) {
    throw "The connected MAC $deviceMac does not match identity $SerialNumber."
}

$writeArgs = @(
    "-m", "esptool", "--chip", $release.chip,
    "--port", $resolvedPort, "--baud", "$Baud",
    "--before", $release.flash_settings.before,
    "--after", $release.flash_settings.after,
    "write-flash", "--flash-mode", $release.flash_settings.mode,
    "--flash-freq", $release.flash_settings.frequency,
    "--flash-size", $release.flash_size
)
foreach ($segment in $resolvedSegments) {
    $writeArgs += @($segment.offset, $segment.path)
    Write-Host "Factory firmware segment: $($segment.name) $($segment.offset)"
}
& $idfPython @writeArgs
if ($LASTEXITCODE -ne 0) {
    throw "Writing the reviewed factory release to $resolvedPort failed."
}

foreach ($segment in $resolvedSegments) {
    & $idfPython -m esptool --chip $release.chip --port $resolvedPort `
        --baud $Baud --before default-reset --after hard-reset `
        verify-flash $segment.offset $segment.path
    if ($LASTEXITCODE -ne 0) {
        throw "Verification failed for $($segment.name) at $($segment.offset)."
    }
}

& $idfPython -m esptool --chip $release.chip --port $resolvedPort `
    --baud $Baud --before default-reset --after hard-reset `
    erase-region $release.wifi_nvs.offset $release.wifi_nvs.size
if ($LASTEXITCODE -ne 0) {
    throw "Clearing the previous Wi-Fi configuration failed."
}

& (Join-Path $PSScriptRoot "flash_device_identity.ps1") `
    -Port $resolvedPort -DeviceSecret $DeviceSecret `
    -IdentityOffset $release.identity.offset -IdentitySize $release.identity.size
if ($LASTEXITCODE -ne 0) {
    throw "Writing the unique device identity failed."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outcomeRoot = Join-Path $projectRoot "run\factory\$timestamp-$SerialNumber"
New-Item -ItemType Directory -Path $outcomeRoot -Force | Out-Null
$outcome = [ordered]@{
    outcome_type = "FactoryUnitOutcome"
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    serial_number = $SerialNumber
    mac = $deviceMac
    release_version = $release.release_version
    profile_id = $release.profile_id
    board_type = $release.board_type
    hardware_version = $release.hardware_version
    port = $resolvedPort
    wifi_cleared = $true
    identity_written = $true
    flash_verified = $true
    qc_status = "hardware-checks-pending"
    qc = [ordered]@{
        screen = "pending"
        microphone = "pending"
        speaker = "pending"
        buttons = "pending"
        network = "pending"
        dialogue = "pending"
    }
    segments = $resolvedSegments | ForEach-Object {
        [ordered]@{
            name = $_.name
            offset = $_.offset
            bytes = $_.bytes
            sha256 = $_.sha256
        }
    }
}
$outcomePath = Join-Path $outcomeRoot "factory-unit-outcome.json"
$outcome | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $outcomePath -Encoding utf8

Write-Output "Factory flash verified on $resolvedPort."
Write-Output "Hardware QC is still required: $outcomePath"
