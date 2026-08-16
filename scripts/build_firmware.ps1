param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("official", "selfhosted")]
    [string]$Variant,

    [string]$BootstrapUrl = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$firmwareRoot = Join-Path $projectRoot "firmware\xiaozhi-esp32"
$boardRelativePath = "main\boards\hensun\hensun-cam-pilot-v1"
$boardRoot = Join-Path $firmwareRoot $boardRelativePath
$baseConfigPath = Join-Path $boardRoot "config.json"
$temporaryConfigName = "config.hensun-build-$PID.json"
$temporaryConfigPath = Join-Path $boardRoot $temporaryConfigName

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

$buildName = if ($Variant -eq "official") {
    "hensun-cam-official-v1"
}
else {
    "hensun-cam-selfhosted-v1"
}

$configName = "config.json"
if ($Variant -eq "selfhosted") {
    $parsedUrl = $null
    if (
        -not [Uri]::TryCreate($BootstrapUrl, [UriKind]::Absolute, [ref]$parsedUrl) -or
        $parsedUrl.Scheme -notin @("http", "https")
    ) {
        throw "Self-hosted firmware requires an absolute HTTP(S) -BootstrapUrl."
    }
    if ($parsedUrl.Host.EndsWith(".invalid")) {
        throw "Self-hosted field firmware cannot use the reserved .invalid endpoint."
    }

    $config = Get-Content -Raw -LiteralPath $baseConfigPath | ConvertFrom-Json
    $build = $config.builds | Where-Object { $_.name -eq $buildName }
    if (-not $build) {
        throw "Build variant $buildName is missing from $baseConfigPath."
    }
    $otaEntry = [string]::Format('CONFIG_OTA_URL="{0}"', $BootstrapUrl.TrimEnd("/"))
    $build.sdkconfig_append = @(
        $build.sdkconfig_append | Where-Object { $_ -notlike "CONFIG_OTA_URL=*" }
    ) + $otaEntry
    $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $temporaryConfigPath -Encoding utf8
    $configName = $temporaryConfigName
}

try {
    Push-Location $firmwareRoot
    $idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $idfPython)) {
        throw "The active ESP-IDF Python environment was not found: $idfPython"
    }
    $firmwareBuildArgs = @(
        "scripts\build.py",
        "hensun/hensun-cam-pilot-v1",
        "--config", $configName,
        "--name", $buildName,
        "--language", "zh-CN",
        "--zip"
    )
    if ($Variant -eq "official") {
        $firmwareBuildArgs += @("--wake-word", "nihaoxiaozhi")
    }
    & $idfPython @firmwareBuildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Firmware build failed for $Variant."
    }

    if ($Variant -eq "selfhosted") {
        $modelAssetsPath = Join-Path $firmwareRoot "build\srmodels\srmodels.bin"
        $emoteAssetsPath = Join-Path $firmwareRoot "build\mmap_build\emote_lab\emote_gen\emote_gen.bin"
        $resourceLimits = @(
            @{ Name = "speech model assets"; Path = $modelAssetsPath; Limit = 0x2FC000 },
            @{ Name = "emote assets"; Path = $emoteAssetsPath; Limit = 5MB }
        )
        foreach ($resource in $resourceLimits) {
            if (-not (Test-Path -LiteralPath $resource.Path)) {
                throw "Missing $($resource.Name): $($resource.Path)"
            }
            $resourceSize = (Get-Item -LiteralPath $resource.Path).Length
            if ($resourceSize -gt $resource.Limit) {
                throw "$($resource.Name) exceeds its partition: $resourceSize > $($resource.Limit) bytes"
            }
        }
    }
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $temporaryConfigPath) {
        Remove-Item -LiteralPath $temporaryConfigPath
    }
}

$versionLine = Select-String -Path (Join-Path $firmwareRoot "CMakeLists.txt") -Pattern 'set\(PROJECT_VER "([^"]+)"\)'
$version = $versionLine.Matches[0].Groups[1].Value
$artifact = Join-Path $firmwareRoot "releases\v${version}_${buildName}.zip"
if (-not (Test-Path -LiteralPath $artifact)) {
    throw "Expected firmware artifact was not created: $artifact"
}

[pscustomobject]@{
    Variant = $Variant
    FirmwareName = $buildName
    Artifact = $artifact
    ModelAssetsBytes = if ($Variant -eq "selfhosted") { (Get-Item -LiteralPath (Join-Path $firmwareRoot "build\srmodels\srmodels.bin")).Length } else { $null }
    EmoteAssetsBytes = if ($Variant -eq "selfhosted") { (Get-Item -LiteralPath (Join-Path $firmwareRoot "build\mmap_build\emote_lab\emote_gen\emote_gen.bin")).Length } else { $null }
}
