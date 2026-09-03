param()

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$stationRoot = Join-Path $env:LOCALAPPDATA "HensunFactory"
$queuePath = Join-Path $stationRoot "identity-queue.json"
$script:currentUnit = $null
$script:currentBoard = $null
$script:currentOutcomePath = $null

function Initialize-EspIdf {
    $activePython = if ($env:IDF_PYTHON_ENV_PATH) {
        Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
    } else {
        ""
    }
    if ($activePython -and (Test-Path -LiteralPath $activePython)) {
        return $activePython
    }

    $idfCandidates = @(
        $env:IDF_PATH,
        "E:\AI_TOY_NATIVE\esp-idf-v6.0.2",
        "C:\Espressif\frameworks\esp-idf-v6.0.2"
    ) | Where-Object { $_ }
    $idfRoot = $idfCandidates | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ "export.ps1")
    } | Select-Object -First 1
    if (-not $idfRoot) {
        throw "未找到 ESP-IDF 6.0.2，请先在出厂电脑安装一次工具链。"
    }
    if (Test-Path -LiteralPath "E:\AI_TOY_TOOLS\espressif") {
        $env:IDF_TOOLS_PATH = "E:\AI_TOY_TOOLS\espressif"
    }
    . (Join-Path $idfRoot "export.ps1")
    $activePython = Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $activePython)) {
        throw "ESP-IDF 已找到，但 Python 环境未能激活。"
    }
    return $activePython
}

function Save-IdentityQueue {
    param([object[]]$Queue)
    New-Item -ItemType Directory -Path $stationRoot -Force | Out-Null
    @($Queue) | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $queuePath -Encoding utf8
}

function Load-IdentityQueue {
    if (-not (Test-Path -LiteralPath $queuePath)) {
        return @()
    }
    return @(Get-Content -LiteralPath $queuePath -Raw -Encoding utf8 | ConvertFrom-Json)
}

function Import-IdentityBatch {
    param([string]$CsvPath)
    $rows = @(Import-Csv -LiteralPath $CsvPath)
    if ($rows.Count -eq 0) {
        throw "身份清单为空。"
    }
    $queue = @(Load-IdentityQueue)
    foreach ($row in $rows) {
        if ($row.board_type -ne "hensun-nocam-pilot-v1") {
            throw "清单包含非当前黄金板型：$($row.board_type)"
        }
        if (-not $row.serial_number -or -not $row.device_secret) {
            throw "清单缺少 serial_number、board_type 或 device_secret。"
        }
        if ($queue.serial_number -contains $row.serial_number) {
            throw "身份已导入，禁止重复使用：$($row.serial_number)"
        }
        $secureSecret = ConvertTo-SecureString $row.device_secret -AsPlainText -Force
        $encrypted = ConvertFrom-SecureString -SecureString $secureSecret
        $queue += [pscustomobject]@{
            serial_number = $row.serial_number
            board_type = $row.board_type
            encrypted_secret = $encrypted
            state = "pending"
            in_progress_mac = ""
            outcome_path = ""
        }
    }
    Save-IdentityQueue $queue
    return $rows.Count
}

function Resolve-Ch340Port {
    $ports = @(
        Get-CimInstance Win32_PnPEntity -ErrorAction Stop |
            Where-Object {
                $_.Name -match '\(COM\d+\)' -and (
                    $_.Name -match 'CH340|CH341' -or $_.PNPDeviceID -match 'VID_1A86'
                )
            } |
            ForEach-Object {
                if ($_.Name -match '\((COM\d+)\)') { $Matches[1].ToUpperInvariant() }
            } |
            Sort-Object -Unique
    )
    if ($ports.Count -ne 1) {
        throw "请只连接一块 CH340/CH341 板，当前检测到 $($ports.Count) 块。"
    }
    return $ports[0]
}

function Get-ConnectedBoard {
    $port = Resolve-Ch340Port
    $idfPython = Initialize-EspIdf
    $chipProbe = (& $idfPython -m esptool --chip esp32s3 --port $port chip-id 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $chipProbe -notmatch 'ESP32-S3') {
        throw "连接的芯片不是 ESP32-S3。"
    }
    $flashProbe = (& $idfPython -m esptool --chip esp32s3 --port $port flash-id 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $flashProbe -notmatch 'Detected flash size:\s*16MB') {
        throw "连接的板不是 16MB Flash。"
    }
    $macMatch = [regex]::Match($chipProbe + $flashProbe, 'MAC:\s*([0-9A-Fa-f:]{17})')
    if (-not $macMatch.Success) {
        throw "读取 MAC 失败。"
    }
    return [pscustomobject]@{
        port = $port
        mac = $macMatch.Groups[1].Value.ToUpperInvariant()
    }
}

function Write-FactoryFailureOutcome {
    param(
        [object]$Unit,
        [object]$Board,
        [string]$Reason,
        [string]$ReleaseManifestPath
    )

    $release = $null
    if (Test-Path -LiteralPath $ReleaseManifestPath -PathType Leaf) {
        $release = Get-Content -LiteralPath $ReleaseManifestPath -Raw -Encoding utf8 | ConvertFrom-Json
    }
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $outcomeRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "run\factory\$timestamp-$($Unit.serial_number)"
    New-Item -ItemType Directory -Path $outcomeRoot -Force | Out-Null
    $outcomePath = Join-Path $outcomeRoot "factory-unit-outcome.json"
    [ordered]@{
        outcome_type = "FactoryUnitOutcome"
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        serial_number = $Unit.serial_number
        mac = if ($Board) { $Board.mac } else { "" }
        release_version = if ($release) { $release.release_version } else { "" }
        profile_id = if ($release) { $release.profile_id } else { "hensun-nocam-pilot-v1" }
        board_type = $Unit.board_type
        port = if ($Board) { $Board.port } else { "" }
        qc_status = "failed"
        failure_reason = $Reason
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $outcomePath -Encoding utf8
    return $outcomePath
}

function Select-IdentityForBoard {
    param([object[]]$Queue, [string]$Mac)
    $retry = $Queue | Where-Object {
        $_.in_progress_mac -eq $Mac -and $_.state -in @("in-progress", "failed")
    } | Select-Object -First 1
    if ($retry) { return $retry }
    $matching = $Queue | Where-Object {
        $_.state -eq "pending" -and $_.serial_number.ToUpperInvariant() -eq $Mac
    } | Select-Object -First 1
    if ($matching) { return $matching }
    return $Queue | Where-Object { $_.state -eq "pending" } | Select-Object -First 1
}

$form = New-Object Windows.Forms.Form
$form.Text = "Hensun 非 CAM 一键出厂"
$form.Size = New-Object Drawing.Size(760, 790)
$form.StartPosition = "CenterScreen"
$form.Font = New-Object Drawing.Font("Microsoft YaHei UI", 10)

$batchLabel = New-Object Windows.Forms.Label
$batchLabel.Text = "一次性身份清单（导入后立即用 Windows 当前账户 DPAPI 加密）"
$batchLabel.Location = New-Object Drawing.Point(24, 22)
$batchLabel.AutoSize = $true
$form.Controls.Add($batchLabel)

$batchPath = New-Object Windows.Forms.TextBox
$batchPath.Location = New-Object Drawing.Point(24, 49)
$batchPath.Size = New-Object Drawing.Size(580, 30)
$form.Controls.Add($batchPath)

$importButton = New-Object Windows.Forms.Button
$importButton.Text = "导入清单"
$importButton.Location = New-Object Drawing.Point(615, 47)
$importButton.Size = New-Object Drawing.Size(110, 34)
$form.Controls.Add($importButton)

$releaseLabel = New-Object Windows.Forms.Label
$releaseLabel.Text = "已审核黄金包 factory-release.json"
$releaseLabel.Location = New-Object Drawing.Point(24, 94)
$releaseLabel.AutoSize = $true
$form.Controls.Add($releaseLabel)

$releasePath = New-Object Windows.Forms.TextBox
$releasePath.Location = New-Object Drawing.Point(24, 121)
$releasePath.Size = New-Object Drawing.Size(580, 30)
$form.Controls.Add($releasePath)

$releaseButton = New-Object Windows.Forms.Button
$releaseButton.Text = "选择黄金包"
$releaseButton.Location = New-Object Drawing.Point(615, 119)
$releaseButton.Size = New-Object Drawing.Size(110, 34)
$form.Controls.Add($releaseButton)

$startButton = New-Object Windows.Forms.Button
$startButton.Text = "开始出厂"
$startButton.Location = New-Object Drawing.Point(24, 170)
$startButton.Size = New-Object Drawing.Size(701, 44)
$form.Controls.Add($startButton)

$log = New-Object Windows.Forms.TextBox
$log.Location = New-Object Drawing.Point(24, 228)
$log.Size = New-Object Drawing.Size(701, 166)
$log.Multiline = $true
$log.ReadOnly = $true
$log.ScrollBars = "Vertical"
$form.Controls.Add($log)

$qcTitle = New-Object Windows.Forms.Label
$qcTitle.Text = "烧录校验完成后，逐项确认硬件质检"
$qcTitle.Location = New-Object Drawing.Point(24, 414)
$qcTitle.AutoSize = $true
$form.Controls.Add($qcTitle)

$qcChecks = @()
$qcLabels = @("屏幕显示正常", "麦克风能识别", "扬声器声音正常", "按键均有反应", "联网并显示在线", "完成 3 轮真实对话")
for ($index = 0; $index -lt $qcLabels.Count; $index++) {
    $check = New-Object Windows.Forms.CheckBox
    $check.Text = $qcLabels[$index]
    $check.Location = New-Object Drawing.Point(28 + (($index % 2) * 330), 448 + ([math]::Floor($index / 2) * 42))
    $check.Size = New-Object Drawing.Size(300, 32)
    $check.Enabled = $false
    $form.Controls.Add($check)
    $qcChecks += $check
}

$passButton = New-Object Windows.Forms.Button
$passButton.Text = "质检通过并结束本台"
$passButton.Location = New-Object Drawing.Point(24, 650)
$passButton.Size = New-Object Drawing.Size(340, 44)
$passButton.Enabled = $false
$form.Controls.Add($passButton)

$failureLabel = New-Object Windows.Forms.Label
$failureLabel.Text = "质检失败原因（失败时填写）"
$failureLabel.Location = New-Object Drawing.Point(24, 580)
$failureLabel.AutoSize = $true
$form.Controls.Add($failureLabel)

$failureReason = New-Object Windows.Forms.TextBox
$failureReason.Location = New-Object Drawing.Point(24, 606)
$failureReason.Size = New-Object Drawing.Size(701, 30)
$failureReason.Enabled = $false
$form.Controls.Add($failureReason)

$failButton = New-Object Windows.Forms.Button
$failButton.Text = "质检失败并记录"
$failButton.Location = New-Object Drawing.Point(385, 650)
$failButton.Size = New-Object Drawing.Size(340, 44)
$failButton.Enabled = $false
$form.Controls.Add($failButton)

$importButton.Add_Click({
    try {
        if (-not $batchPath.Text) {
            $dialog = New-Object Windows.Forms.OpenFileDialog
            $dialog.Filter = "CSV 清单 (*.csv)|*.csv"
            if ($dialog.ShowDialog() -ne "OK") { return }
            $batchPath.Text = $dialog.FileName
        }
        $count = Import-IdentityBatch -CsvPath $batchPath.Text
        $log.AppendText("已安全导入并加密 $count 个设备身份。`r`n")
        $batchPath.Clear()
    } catch {
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "导入失败") | Out-Null
    }
})

$releaseButton.Add_Click({
    $dialog = New-Object Windows.Forms.OpenFileDialog
    $dialog.Filter = "Hensun 黄金包 (factory-release.json)|factory-release.json"
    if ($dialog.ShowDialog() -eq "OK") { $releasePath.Text = $dialog.FileName }
})

$startButton.Add_Click({
    try {
        if (-not (Test-Path -LiteralPath $releasePath.Text -PathType Leaf)) {
            throw "请先选择已审核的黄金包。"
        }
        $startButton.Enabled = $false
        $log.AppendText("正在识别板子、MAC、芯片和容量…`r`n")
        [Windows.Forms.Application]::DoEvents()
        $board = Get-ConnectedBoard
        $script:currentBoard = $board
        $queue = @(Load-IdentityQueue)
        $unit = Select-IdentityForBoard -Queue $queue -Mac $board.mac
        if (-not $unit) { throw "没有可用的设备身份，请先导入新清单。" }
        $unit.state = "in-progress"
        $unit.in_progress_mac = $board.mac
        Save-IdentityQueue $queue
        $script:currentUnit = $unit

        $secureSecret = ConvertTo-SecureString $unit.encrypted_secret
        $log.AppendText("开始烧录 $($unit.serial_number)，中断后只会重试当前 MAC。`r`n")
        [Windows.Forms.Application]::DoEvents()
        $output = & (Join-Path $PSScriptRoot "factory_flash.ps1") `
            -ReleaseManifestPath $releasePath.Text `
            -SerialNumber $unit.serial_number `
            -DeviceSecret $secureSecret `
            -Port $board.port `
            -ConfirmFactoryReset 2>&1
        if ($LASTEXITCODE -ne 0) { throw ($output | Out-String) }
        $outcomeLine = $output | Where-Object { $_ -match 'Hardware QC is still required:' } | Select-Object -Last 1
        if (-not $outcomeLine) { throw "烧录完成但未生成质检报告。" }
        $script:currentOutcomePath = ($outcomeLine -replace '^.*Hardware QC is still required:\s*', '').Trim()
        $unit.outcome_path = $script:currentOutcomePath
        Save-IdentityQueue $queue
        $log.AppendText("固件、资源、身份和 Wi-Fi 清理已校验。请完成下方质检。`r`n")
        foreach ($check in $qcChecks) { $check.Enabled = $true; $check.Checked = $false }
        $failureReason.Enabled = $true
        $failureReason.Clear()
        $passButton.Enabled = $true
        $failButton.Enabled = $true
    } catch {
        if ($script:currentUnit) {
            $queue = @(Load-IdentityQueue)
            $failed = $queue | Where-Object { $_.serial_number -eq $script:currentUnit.serial_number } | Select-Object -First 1
            if ($failed) {
                $failed.state = "failed"
                $failed.outcome_path = Write-FactoryFailureOutcome `
                    -Unit $failed -Board $script:currentBoard `
                    -Reason $_.Exception.Message -ReleaseManifestPath $releasePath.Text
                Save-IdentityQueue $queue
            }
        }
        $log.AppendText("失败：$($_.Exception.Message)`r`n")
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "出厂失败") | Out-Null
    } finally {
        $startButton.Enabled = $true
    }
})

$passButton.Add_Click({
    if (@($qcChecks | Where-Object { -not $_.Checked }).Count -ne 0) {
        [Windows.Forms.MessageBox]::Show("六项质检全部通过后才能结束本台。", "质检未完成") | Out-Null
        return
    }
    try {
        $outcome = Get-Content -LiteralPath $script:currentOutcomePath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($outcome.outcome_type -ne "FactoryUnitOutcome") { throw "质检报告类型错误。" }
        $outcome.qc_status = "passed"
        $outcome.qc.screen = "passed"
        $outcome.qc.microphone = "passed"
        $outcome.qc.speaker = "passed"
        $outcome.qc.buttons = "passed"
        $outcome.qc.network = "passed"
        $outcome.qc.dialogue = "3-turns-passed"
        $outcome | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $script:currentOutcomePath -Encoding utf8

        $queue = @(Load-IdentityQueue)
        $completed = $queue | Where-Object { $_.serial_number -eq $script:currentUnit.serial_number } | Select-Object -First 1
        $completed.state = "consumed"
        $completed.encrypted_secret = ""
        Save-IdentityQueue $queue
        $log.AppendText("本台质检通过，身份已标记为不可复用。可以拔下并连接下一块。`r`n")
        foreach ($check in $qcChecks) { $check.Enabled = $false }
        $failureReason.Enabled = $false
        $passButton.Enabled = $false
        $failButton.Enabled = $false
        $script:currentUnit = $null
        $script:currentBoard = $null
        $script:currentOutcomePath = $null
    } catch {
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "保存质检失败") | Out-Null
    }
})

$failButton.Add_Click({
    $reason = $failureReason.Text.Trim()
    if (-not $reason) {
        [Windows.Forms.MessageBox]::Show("请填写具体失败原因，便于返修和原 MAC 重试。", "缺少失败原因") | Out-Null
        return
    }
    try {
        $outcome = Get-Content -LiteralPath $script:currentOutcomePath -Raw -Encoding utf8 | ConvertFrom-Json
        if ($outcome.outcome_type -ne "FactoryUnitOutcome") { throw "质检报告类型错误。" }
        $outcome.qc_status = "failed"
        $outcome | Add-Member -NotePropertyName failure_reason -NotePropertyValue $reason -Force
        $qcNames = @("screen", "microphone", "speaker", "buttons", "network", "dialogue")
        for ($index = 0; $index -lt $qcNames.Count; $index++) {
            $outcome.qc.($qcNames[$index]) = if ($qcChecks[$index].Checked) { "passed" } else { "failed" }
        }
        $outcome | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $script:currentOutcomePath -Encoding utf8

        $queue = @(Load-IdentityQueue)
        $failed = $queue | Where-Object { $_.serial_number -eq $script:currentUnit.serial_number } | Select-Object -First 1
        $failed.state = "failed"
        Save-IdentityQueue $queue
        $log.AppendText("本台质检失败已记录；仅连接同一 MAC 时复用此身份重试。`r`n")
        foreach ($check in $qcChecks) { $check.Enabled = $false }
        $failureReason.Enabled = $false
        $passButton.Enabled = $false
        $failButton.Enabled = $false
        $script:currentUnit = $null
        $script:currentBoard = $null
        $script:currentOutcomePath = $null
    } catch {
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, "保存质检失败") | Out-Null
    }
})

[void]$form.ShowDialog()
