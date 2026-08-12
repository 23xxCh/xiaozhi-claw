param(
    [Parameter(Mandatory = $true)]
    [SecureString]$DeviceSecret,

    [string]$Port = "COM6"
)

$ErrorActionPreference = "Stop"
if (-not $env:IDF_PATH) {
    throw "IDF_PATH is not set. Open an ESP-IDF terminal first."
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
    & python $generator generate $csvPath $binaryPath 0x4000
    if ($LASTEXITCODE -ne 0) {
        throw "Generating the per-device NVS partition failed."
    }
    & esptool.py --port $Port write_flash 0x9000 $binaryPath
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
