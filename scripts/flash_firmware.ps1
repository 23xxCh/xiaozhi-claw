param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("official", "selfhosted", "selfhosted-landscape", "selfhosted-portrait")]
    [string]$Variant,

    [string]$Port = "COM6",
    [string]$BootstrapUrl = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$firmwareRoot = Join-Path $projectRoot "firmware\xiaozhi-esp32"

$buildArgs = @{ Variant = $Variant }
if ($Variant -ne "official") {
    $buildArgs.BootstrapUrl = $BootstrapUrl
}
$result = & (Join-Path $PSScriptRoot "build_firmware.ps1") @buildArgs

Push-Location $firmwareRoot
try {
    if ($Variant -ne "official") {
        $idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
        $appPath = Join-Path $firmwareRoot "build\xiaozhi.bin"
        $emotePath = $result.EmoteAssetsPath
        foreach ($requiredPath in ($idfPython, $appPath, $emotePath)) {
            if (-not (Test-Path -LiteralPath $requiredPath)) {
                throw "Profile-selected self-hosted flash input is missing: $requiredPath"
            }
        }
        & $idfPython -m esptool --chip esp32s3 --port $Port write_flash `
            0x20000 $appPath `
            0xB00000 $emotePath
    }
    else {
        idf.py -p $Port flash
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Flashing $($result.FirmwareName) to $Port failed."
    }
}
finally {
    Pop-Location
}

$result
