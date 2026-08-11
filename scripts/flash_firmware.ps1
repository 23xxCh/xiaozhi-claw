param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("official", "selfhosted")]
    [string]$Variant,

    [string]$Port = "COM6",
    [string]$BootstrapUrl = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$firmwareRoot = Join-Path $projectRoot "firmware\xiaozhi-esp32"

$buildArgs = @{ Variant = $Variant }
if ($Variant -eq "selfhosted") {
    $buildArgs.BootstrapUrl = $BootstrapUrl
}
$result = & (Join-Path $PSScriptRoot "build_firmware.ps1") @buildArgs

Push-Location $firmwareRoot
try {
    idf.py -p $Port flash
    if ($LASTEXITCODE -ne 0) {
        throw "Flashing $($result.FirmwareName) to $Port failed."
    }
}
finally {
    Pop-Location
}

$result
