# 本地金样机运行、刷机与恢复手册

> 适用于 2026-08-18 以后从 `feature/hensun-stability-quality` 继续开发的当前 ESP32-S3 CAM 金样机。

## 1. 操作边界

- 只在 `E:\HENSUN_STABILITY_WT` 开发。
- 不要直接修改 `E:\AI TOY\xiaozhi-claw`。
- 当前设备通过 CH340 暴露为 `COM6`；每次刷机前重新检查，不要假设端口永远不变。
- 普通固件与表情更新不得擦除 NVS、Wi-Fi、设备身份或密钥。
- 当前先验证本地闭环，不部署 Staging，不切正式域名。
- `.env`、模型 Key、SMTP 授权码和设备密钥不得复制到命令输出、文档或 Git。

## 2. 当前金样机快照

2026-08-28 当前可聊天版本的源码、数据库和二进制快照位于：

```text
E:\HENSUN_STABILITY_WT\run\backups\20260828-200611-current-chat-freeze
```

其中 `xiaozhi.bin` 是当前应用，`emote-current-standard-shy-sad.bin` 是当前 20 索引测试表情包；`emote_gen.bin` 只是仓库构建产物，不是当前真机测试表情包。具体大小和哈希以同目录 `sha256-manifest.json` 为准。

以下 2026-08-18 快照继续作为旧版回退基线：

本机可恢复文件位于：

```text
E:\HENSUN_STABILITY_WT\run\backups\golden-local-face-20260818
```

`run/` 被 Git 忽略，这份快照只在当前电脑上存在。清理磁盘前必须先确认是否仍需要。

| 文件 | 地址 | 大小 | SHA256 |
|---|---:|---:|---|
| `xiaozhi.bin` | `0x20000` | 2699248 | `45A4F6FFB20BCE4304E547D280D3B92C7CEF4A94E33713D48DCCE75A73FC6192` |
| `emote_gen.bin` | `0xB00000` | 2837200 | `4A0E2C6B1A42B620BF615FD99ED2F86EB6C0BB3146482E07C4844126A7D5AF85` |
| 完整 ZIP | 仅归档 | 10429154 | `3614CF4651B7CE0A8DB91A8C6F9EC24FC869235F6B30B922DCEF05968D866A32` |

设备识别信息：ESP32-S3 rev 0.2、8MB PSRAM、MAC `28:84:85:4a:3d:b8`。不要把 MAC 当作设备密钥。

## 3. 启动本地系统

### 前提

- 当前电脑拥有 `192.168.5.49`。
- 根目录存在未提交的 `.env`，`web/.env.local` 也已配置。
- `.venv`、Node.js 和前端依赖已经安装。
- ESP32 和电脑网络互通。

启动：

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
.\scripts\start_local_pilot.ps1 -HostAddress '192.168.5.49'
```

脚本会：

1. 备份 `hensun-lan.db`。
2. 执行 Alembic 迁移。
3. 构建 Next.js 生产版本。
4. 精确记录三个子进程 PID。
5. 启动前端 `3000`、控制面 `8000`、实时网关 `8001`。
6. 等待三个健康检查通过。

只检查，不打开浏览器：

```powershell
.\scripts\start_local_pilot.ps1 -NoBrowser
Invoke-WebRequest http://127.0.0.1:8000/health/ready -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8001/health/ready -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:3000 -UseBasicParsing
```

停止：

```powershell
.\scripts\stop_local_pilot.ps1
```

不要按端口批量杀进程。启动脚本把 PID 和日志位置写入 `run/local-pilot/processes.json`，停止脚本只处理这些受控进程。

## 4. 构建横屏本地固件

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
.\scripts\build_firmware.ps1 `
  -Variant local `
  -BootstrapUrl 'http://192.168.5.49:8000/v1/device/xiaozhi-bootstrap'
```

构建必须满足：

- 固件名为 `hensun-cam-selfhosted-landscape-local-v1`。
- 应用放得进最小 OTA 分区。
- `srmodels.bin` 不超过语音模型分区。
- `emote_gen.bin` 不超过 5MB。
- 临时 `hensun-build-*.json` 写在板目录内，名称不会被 `config*.json` 扫描命中，并在结束后删除。
- 输出 ZIP 位于 `firmware/xiaozhi-esp32/releases/`，但构建产物和 ZIP 不应提交。

构建后先记录哈希：

```powershell
Get-FileHash firmware\xiaozhi-esp32\build\xiaozhi.bin -Algorithm SHA256
Get-FileHash firmware\xiaozhi-esp32\build\mmap_build\emote_lab\emote_gen\emote_gen.bin -Algorithm SHA256
```

## 5. 刷机前检查

```powershell
Get-PnpDevice -PresentOnly |
  Where-Object FriendlyName -like '*CH340*'
```

如果需要确认芯片，但不写 Flash：

```powershell
python -m esptool --chip esp32s3 --port COM6 chip-id
```

如果自动进入下载模式失败：按住 BOOT，轻按 RST，写入开始后松开 BOOT。不要因为一次连接失败就执行整片擦除。

## 6. 只更新应用和表情

表情或显示逻辑迭代优先只刷以下两个分区：

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
python -m esptool `
  --chip esp32s3 `
  --port COM6 `
  --baud 460800 `
  --before default-reset `
  --after hard-reset `
  write-flash `
  --flash-mode dio `
  --flash-freq 80m `
  --flash-size 16MB `
  0x20000 firmware\xiaozhi-esp32\build\xiaozhi.bin `
  0xB00000 firmware\xiaozhi-esp32\build\mmap_build\emote_lab\emote_gen\emote_gen.bin
```

如果当前 Python 没安装 esptool，应先激活 ESP-IDF 6.0.2 环境，不要随意改用不明版本工具。

这条命令不会写入 NVS、设备密钥、Wi-Fi、分区表或语音模型。写完必须看到两个 `Hash of data verified`。

## 7. 恢复 2026-08-18 金样机

```powershell
$backup = 'E:\HENSUN_STABILITY_WT\run\backups\golden-local-face-20260818'

python -m esptool `
  --chip esp32s3 `
  --port COM6 `
  --baud 460800 `
  --before default-reset `
  --after hard-reset `
  write-flash `
  --flash-mode dio `
  --flash-freq 80m `
  --flash-size 16MB `
  0x20000 "$backup\xiaozhi.bin" `
  0xB00000 "$backup\emote_gen.bin"
```

恢复后不要重新配网，除非日志明确说明 Wi-Fi 配置不存在。

## 8. 启动日志验收

至少确认以下事实：

- SKU 是 `hensun-cam-selfhosted-landscape-local-v1`。
- `emote_gen` 分区挂载成功。
- 没有 `missing animation` 或 `mmap has no file`。
- Wi-Fi 获得 IP。
- Bootstrap 指向本机 `192.168.5.49:8000`。
- `mn5q8_cn` 和自定义 `ni hao xiao can` 已加载。
- 没有看门狗重启、分区越界或持续内存下降。

不要记录 Wi-Fi 密码、设备密钥、WSS 令牌或用户语音内容。

## 9. 自动测试

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
$py = .\.venv\Scripts\python.exe

& $py -m pytest backend/tests -q
& $py -m unittest discover `
  -s firmware/xiaozhi-esp32/scripts/tests `
  -p 'test_hensun*.py'
& $py scripts/release_gate.py
```

表情或资源修改还必须重新执行完整 ESP-IDF 构建。主机测试通过不代表真机通过。

## 10. 真机回归顺序

每次只改变一个变量，按下面顺序记录结果：

1. 十次连续说“你好小灿 + 一个短问题”，至少九次一次成功。
2. 唤醒后停顿，再说完整问题，确认不漏开头。
3. 中性、开心、关怀各三轮，确认脸部不突然重播入场动画。
4. 长句、数字、英文、时间、天气各一轮。
5. 回复时按 BOOT，旧声音必须立即停止。
6. 说“小灿闭嘴”，设备必须进入软待机。
7. 回复结束后 0.8 秒收尾并经过余响保护，再开放 10 秒续聊；第 9.9 秒开始讲话也必须录完整句，无人讲话后静默进入睡眠。
8. 连续 30 轮无沉默、断音、旧音频串轮、无声说话脸或 TFT 卡死。
9. 连续运行 2 小时，再做 8 小时待机检查。

当前用户只确认了最新视觉和基本对话“可以”；30 轮和长稳测试尚未完成，不能把金样机标记为量产验收通过。

## 11. 已知故障与定位入口

### 表情资源提示 `mmap has no file`

资源索引的文件名槽为 16 字节，文件名必须为结尾空字符留一个字节。`talk_neutral.eaf` 恰好占满 16 字节，会被播放器当作无效字符串；运行时已改用 `talk_base.eaf`。打包测试会检查每个槽都包含 `NUL`。

### 表情比声音先动

检查 `tts.start` 是否只进入 awaiting-audio；只有第一块实际扬声器 PCM 才能调用 `SetSpeechLevel` 并进入说话脸。不要用 LLM 情绪或 `tts.start` 直接触发 `speaking`。

### 有说话脸但无声音

检查 `reply_id/turn_id`、`ready`、第一块 PCM、`stop`、本地队列排空和 `drained` 的顺序。旧回合确认必须被忽略；播放队列丢包应为零。

### 回复后仍停在说话脸

结束依据是本地解码和扬声器真正排空，不是服务器发出 `tts.stop`。匹配的 `drained` 后才进入收尾和继续聆听。

### 表情突然插入旧状态

显示切换队列长度为 1，只保留最新请求；每个请求携带 generation。不要恢复为积压队列，也不要在一次回复中反复重启整张脸的动画。

### 嘴型怪异或不连贯

当前接受的是细线 U 形五档嘴型。不要重新引入白色圆环、实心椭圆或完全不同形状的随机跳变。嘴型只读 PCM 包络，并按 20 FPS 平滑移动一档。

### 黑屏或看似卡住

先读取串口日志，区分显示任务、资源挂载、Wi-Fi 和整个 MCU 是否仍运行。必要时断电重插；不要未经诊断就擦除 Flash。若修改了显示画布，确认 `display_profiles.json` 与表情资源都是 320×240。
