param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("official", "selfhosted", "selfhosted-landscape", "selfhosted-portrait", "emote-lab")]
    [string]$Variant,

    [string]$BootstrapUrl = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$firmwareRoot = Join-Path $projectRoot "firmware\xiaozhi-esp32"

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

$buildName = switch ($Variant) {
    "official" { "hensun-cam-official-v1" }
    "selfhosted" { "hensun-cam-selfhosted-v1" }
    "selfhosted-landscape" { "hensun-cam-selfhosted-landscape-v1" }
    "selfhosted-portrait" { "hensun-cam-selfhosted-portrait-v1" }
    "emote-lab" { "hensun-cam-emote-lab-v1" }
}
$isSelfHosted = $Variant -ne "official"

if ($isSelfHosted) {
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
}

$previousBootstrapUrl = $env:HENSUN_BOOTSTRAP_URL
try {
    if ($isSelfHosted) {
        $env:HENSUN_BOOTSTRAP_URL = $BootstrapUrl.TrimEnd("/")
    }
    else {
        Remove-Item Env:HENSUN_BOOTSTRAP_URL -ErrorAction SilentlyContinue
    }

    Push-Location $firmwareRoot
    try {
        $idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $idfPython)) {
            throw "The active ESP-IDF Python environment was not found: $idfPython"
        }
        $firmwareBuildArgs = @(
            "scripts\build.py",
            "hensun/hensun-cam-pilot-v1",
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
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -eq $previousBootstrapUrl) {
        Remove-Item Env:HENSUN_BOOTSTRAP_URL -ErrorAction SilentlyContinue
    }
    else {
        $env:HENSUN_BOOTSTRAP_URL = $previousBootstrapUrl
    }
}

$generatedMetadataPath = Join-Path $firmwareRoot "build\generated\hensun_profile.json"
if (-not (Test-Path -LiteralPath $generatedMetadataPath)) {
    throw "Generated Hensun Profile metadata is missing: $generatedMetadataPath"
}
$generatedMetadata = Get-Content -Raw -LiteralPath $generatedMetadataPath | ConvertFrom-Json
$modelAssetsPath = Join-Path $firmwareRoot "build\srmodels\srmodels.bin"
$emoteAssetsPath = Join-Path $firmwareRoot (
    "build\mmap_build\{0}\emote_gen\emote_gen.bin" -f $generatedMetadata.emote_asset_directory
)
if ($isSelfHosted) {
    $resourceLimits = @(
        @{ Name = "speech model assets"; Path = $modelAssetsPath; Limit = [int64]$generatedMetadata.model_max_bytes },
        @{ Name = "emote assets"; Path = $emoteAssetsPath; Limit = [int64]$generatedMetadata.emote_max_bytes }
    )
    foreach ($resource in $resourceLimits) {
        if (-not (Test-Path -LiteralPath $resource.Path)) {
            throw "Missing $($resource.Name): $($resource.Path)"
        }
        $resourceSize = (Get-Item -LiteralPath $resource.Path).Length
        if ($resourceSize -gt $resource.Limit) {
            throw "$($resource.Name) exceeds its Profile partition limit: $resourceSize > $($resource.Limit) bytes"
        }
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
    HardwareProfile = $generatedMetadata.hardware_profile_id
    DisplayProfile = $generatedMetadata.display_profile_id
    ProductVariant = $generatedMetadata.product_variant_id
    ProfileSha256 = $generatedMetadata.profile_sha256
    ModelAssetsBytes = if ($isSelfHosted) { (Get-Item -LiteralPath $modelAssetsPath).Length } else { $null }
    EmoteAssetsBytes = if ($isSelfHosted) { (Get-Item -LiteralPath $emoteAssetsPath).Length } else { $null }
    EmoteAssetsPath = if ($isSelfHosted) { $emoteAssetsPath } else { $null }
}
