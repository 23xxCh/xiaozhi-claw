param(
    [Parameter(Mandatory = $true)]
    [SecureString]$DeviceSecret,

    [string]$Port = "COM6"
)

$ErrorActionPreference = "Stop"
if ([System.IO.Ports.SerialPort]::GetPortNames() -notcontains $Port) {
    throw "Serial port $Port is not available. Reconnect the ESP32 before provisioning it."
}
if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    $idfCandidates = @(
        $env:IDF_PATH,
        "E:\AI_TOY_NATIVE\esp-idf-v6.0.2",
        "C:\Espressif\frameworks\esp-idf-v6.0.2"
    ) | Where-Object { $_ }
    $idfRoot = $idfCandidates | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ "export.ps1")
    } | Select-Object -First 1
    if (-not $idfRoot) {
        throw "ESP-IDF 6.0.2 is not active and no local export.ps1 was found."
    }
    if (Test-Path -LiteralPath "E:\AI_TOY_TOOLS\espressif") {
        $env:IDF_TOOLS_PATH = "E:\AI_TOY_TOOLS\espressif"
    }
    . (Join-Path $idfRoot "export.ps1")
}
$idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $idfPython)) {
    throw "The active ESP-IDF Python environment was not found: $idfPython"
}
$plainSecret = [Net.NetworkCredential]::new("", $DeviceSecret).Password
if ($plainSecret.Length -lt 32) {
    throw "DeviceSecret is too short. Use the one-time value returned by the factory API."
}

$generator = Join-Path $env:IDF_PATH "components\nvs_flash\nvs_partition_generator\nvs_partition_gen.py"
if (-not (Test-Path -LiteralPath $generator)) {
    throw "NVS generator not found at $generator"
}

$temporaryRoot = Join-Path $env:TEMP ("hensun-nvs-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
$csvPath = Join-Path $temporaryRoot "identity.csv"
$binaryPath = Join-Path $temporaryRoot "identity.bin"

try {
    $csv = @(
        "key,type,encoding,value"
        "hensun,namespace,,"
        "device_secret,data,string,$plainSecret"
    )
    Set-Content -LiteralPath $csvPath -Value $csv -Encoding utf8NoBOM
    & $idfPython $generator generate $csvPath $binaryPath 0x4000
    if ($LASTEXITCODE -ne 0) {
        throw "Generating the per-device NVS partition failed."
    }
    & $idfPython -m esptool --port $Port write_flash 0x800000 $binaryPath
    if ($LASTEXITCODE -ne 0) {
        throw "Writing the per-device NVS partition to $Port failed."
    }
    Write-Output "Device identity written to $Port. The temporary plaintext partition was removed."
}
finally {
    $plainSecret = $null
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemporaryRoot = (Resolve-Path -LiteralPath $temporaryRoot).Path
        $resolvedSystemTemp = (Resolve-Path -LiteralPath $env:TEMP).Path.TrimEnd("\") + "\"
        if (-not $resolvedTemporaryRoot.StartsWith($resolvedSystemTemp, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a temporary path outside the system temp directory."
        }
        Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force
    }
}
