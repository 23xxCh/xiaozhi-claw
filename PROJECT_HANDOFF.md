# Hensun Desk 项目交接说明

> 状态核对时间：2026-08-19（Asia/Hong_Kong）  
> 当前阶段：单台本地金样机稳定性收口  
> 当前工作目录：`E:\HENSUN_STABILITY_WT`  
> 当前分支：`feature/hensun-stability-quality`  
> 当前实现基线：请用 `git log -1 --oneline` 复核。实时 ASR 已改为 Manual 模式；真机仍运行 10:56 金样机，尚未刷入当前本地构建

本文是换账号、换 Agent 后继续开发时的单文件入口。它只保留有效背景、当前事实、不可擅改的决定和下一步，不包含闲聊、密钥或已被后续决定推翻的方案。

## 1. 项目目标

### 长期目标

开发一款面向 18 岁以上用户的 Hensun Desk 桌面 AI 助理与轻陪伴设备，形成完整的自有产品闭环：

```text
ESP32-S3 CAM 设备
→ 自有 Bootstrap / WSS / OTA
→ 自有用户、设备、助手和配置后台
→ 实时 ASR / LLM / TTS
→ 原创横屏表情与真实语音口型
→ 工厂身份写入、批量烧录和后续量产
```

产品要做到普通客户能够“插电、配网、绑定、说话”，服务商地址、API Key 和设备密钥不能暴露给客户。

### 当前阶段目标

当前不扩展新功能，先把一台 ESP32-S3 CAM 做成可恢复、可重复验收的本地金样机：

```text
说“你好小灿”或按 BOOT
→ 快速进入聆听
→ 完整收音
→ Qwen 实时 ASR
→ DeepSeek 流式回答
→ Qwen 实时 TTS
→ 第一帧真实声音出现时才显示说话
→ 声音结束后收尾并继续聆听
→ 10 秒无人讲话后进入软睡眠
```

当前优先级是语音稳定、表情同步、TFT 长稳、测试和提交收口，不是搜索、声纹、支付、小程序、Staging 或继续增加表情数量。

## 2. 已经完成的工作

### 硬件与固件

- 当前硬件已识别为 ESP32-S3 CAM，16MB Flash、8MB PSRAM。
- CH340 驱动可用；当前设备通常显示为 `USB-SERIAL CH340 (COM6)`。COM 号可能变化，刷机前必须重新检查。
- 当前板型入口为 `hensun-cam-pilot-v1`。
- 保留摄像头代码和远程拍照能力；空闲时不持续运行摄像头 DMA。
- 已保留小智官网回退版、自有服务版、本地横屏版和表情实验版，固件名与 OTA 通道隔离。
- 当前金样机固件：`hensun-cam-selfhosted-landscape-local-v1`。
- 当前显示：ST7789，逻辑分辨率 320×240 横屏。
- 当前本地唤醒词：“你好小灿”，MultiNet 命令为 `ni hao xiao can`。
- BOOT 待机时开始对话；活动时停止当前对话并进入软待机；GPIO0 仍用于下载模式。

### 自有后端与网页

- 已建立 FastAPI 控制面与独立 FastAPI/ASGI 实时网关，保持两个进程。
- 控制面已覆盖邮箱验证码登录、用户、设备认领、助手、配置、用量、记忆、OTA、审计和内部后台基础能力。
- 实时网关已覆盖设备 WSS、Qwen ASR、DeepSeek LLM、Qwen TTS、打断、播放确认、工具和表情控制。
- 已有 Next.js + TypeScript 客户控制台，客户和内部后台入口按权限隔离。
- 本地使用 SQLite；服务器部署目标使用独立 MySQL 8 数据库和账户。
- 本地三个服务当前实测均为 HTTP 200：

  | 服务 | 地址 |
  |---|---|
  | 前端 | `http://127.0.0.1:3000` |
  | 控制面 | `http://127.0.0.1:8000/health/ready` |
  | 实时网关 | `http://127.0.0.1:8001/health/ready` |

- 三个进程由 `run/local-pilot/processes.json` 精确记录 PID；不得按端口批量杀进程。

### 实时语音与播放

- ESP32 上行保持 16kHz、单声道、60ms Opus；服务端为 Qwen 实时 ASR 增量封装 Ogg-Opus。
- 下行保持 24kHz、单声道、60ms Opus。
- 实时 ASR 失败时，可使用同一轮内存音频走批量 Qwen ASR 降级。
- TTS 音频按 60ms 匀速发送，使用队列背压，禁止突发追帧。
- 播放握手固定为：

  ```text
  tts.start → ready → PCM → tts.stop → drained
  ```

- `turn_id + reply_id` 用于隔离旧轮次消息。
- 第一块真正送入扬声器的 PCM 才触发 `speaking`，不是收到 `tts.start` 就触发。
- 本地解码器和扬声器队列真正排空后才发送 `drained`。
- 回复结束后显示约 0.8 秒收尾，再聆听 10 秒；无人讲话后进入软睡眠。
- “小灿闭嘴”保留为服务端停止对话意图。

### 表情与口型

- 当前表情包包含 12 个系统/展示状态。
- 后端支持 10 类受控 `[[face:...]]` 对话标签，每轮最多两次，并从 TTS、用户正文和日志中剥离。
- 当前真机只完整接入三套独立说话脸：`talk_base`、`talk_happy`、`talk_caring`。
- 当前嘴型为独立 PCM 叠加层，不生成“情绪数 × 嘴型数”的全屏动画组合。
- 用户已确认当前细线 U 形五档嘴型和最新横屏说话脸效果“可以了”。
- 嘴型按 20 FPS 更新，每 50ms 最多变化一档；连续静音超过 300ms 闭嘴。
- 表情切换采用容量为 1 的 mailbox 和 generation，旧请求不能积压覆盖新状态。

### 工程化与恢复能力

- 已有 GitHub Actions：
  - `.github/workflows/quality.yml`：后端、前端、Playwright/Axe、契约与固件主机测试。
  - `.github/workflows/firmware-release.yml`：手动/定时 ESP-IDF 固件矩阵构建，包含本地横屏版；产物明确标记 CI-only，不能现场下发。
  - `.github/workflows/deploy-staging.yml`：必须人工确认的 Staging 发布。
- `scripts/flash_firmware.ps1` 会在写入前重新识别 CH340/CH341，只允许写 `app` 和 `emote_gen`；不会写 bootloader、分区表、OTA 数据、模型、NVS 或设备身份。
- 当前金样机恢复包位于：

  ```text
  E:\HENSUN_STABILITY_WT\run\backups\golden-local-face-20260818
  ```

  | 文件 | Flash 地址 | SHA256 |
  |---|---:|---|
  | `xiaozhi.bin` | `0x20000` | `45A4F6FFB20BCE4304E547D280D3B92C7CEF4A94E33713D48DCCE75A73FC6192` |
  | `emote_gen.bin` | `0xB00000` | `4A0E2C6B1A42B620BF615FD99ED2F86EB6C0BB3146482E07C4844126A7D5AF85` |
  | 完整 ZIP | 不直接写入 | `3614CF4651B7CE0A8DB91A8C6F9EC24FC869235F6B30B922DCEF05968D866A32` |

### 已有验证证据

- 五个实现提交已按暂存区快照隔离验证：实时语音、对话表情、固件显示、安全刷写、CI。
- 对话表情解析相关 32 项、刷写/板型相关 33 项及全部固件主机测试通过。
- 表情源、运行时资源、BIN 哈希与 320×240/20 FPS 约束通过一致性检查。
- `scripts/release_gate.py`：通过（仅源码/配置门禁）。
- 旧金样机应用和表情分区曾通过 esptool 哈希校验；串口曾确认表情资源、Wi-Fi、Bootstrap、MultiNet 和“你好小灿”加载成功。
- 用户已完成上一版视觉和基础对话人工确认。
- **最新提交的 ESP-IDF 三版本完整构建和真机刷写仍未完成，不能引用旧构建替代。**

这些证据不等于 30 轮对话或长稳验收已经通过。

## 3. 当前代码和文件状态

### Git 状态

- 工作目录：`E:\HENSUN_STABILITY_WT`
- 分支：`feature/hensun-stability-quality`
- 当前分支没有配置 upstream，未推送、未创建 PR。
- 远端：`https://github.com/23xxCh/xiaozhi-claw.git`
- 已生成忽略目录下的可恢复工作树快照：`run/backups/worktree-snapshot-20260818-191118/`。
- 已完成的本地实现提交：
  1. `cc317bf fix(realtime): stabilize ASR and playback lifecycle`
  2. `3cd38b8 feat(realtime): add bounded dialogue face controls`
  3. `4f86228 feat(firmware): synchronize speech and landscape expressions`
  4. `ff36dcd build(firmware): harden local landscape build and safe flashing`
  5. `ca69cc3 ci: cover local golden sample`
- 本交接文档及相关金样机入口文档是第六个独立提交。
- 评审债收口后又补了本地提交：
  1. `959b8e4 fix(realtime): recognize 小灿闭嘴 as soft standby`
  2. `99522cd fix(config): point default device WS to gateway 8001`
  3. `4a23fc6 docs: refresh golden-sample status after review`
  4. `d8594e6 fix(realtime): use Qwen ASR Manual mode instead of commit-in-VAD`
  5. 本文档更新（以 `git log -1 --oneline` 为准）
- `migrations/versions/20260815_06_device_config_contract.py` 与 `design/` 明确排除，仍不得混入、删除或擅自提交。
- 禁止 reset、checkout 覆盖、清理未跟踪目录或整批 `git add -A`。

提交时禁止 `git add -A`。必须逐组检查、测试和暂存。

### 其他 worktree

以下目录属于其他工作线，不要同步覆盖或删除：

| 路径 | 分支/用途 |
|---|---|
| `E:\AI TOY\xiaozhi-claw` | `feature/commercial-pilot-foundation`，禁止直接修改 |
| `E:\HENSUN_FRONTEND_V2_WT` | 客户前端 V2 |
| `E:\HENSUN_LANDSCAPE_WT` | 横屏表情历史工作线 |
| `E:\HENSUN_LANDSCAPE_VERIFY` | 分离的验证 worktree |
| `E:\HENSUN_STABILITY_WT` | 当前唯一活动主线 |

## 4. 已确定的技术方案和不能擅自改变的要求

以下要求只有用户重新明确批准后才能改变。

### 产品与隐私

- 当前公开产品是 18+ 成人桌面 AI 助理与轻陪伴。
- 家庭模式、支付、微信小程序、声纹和云端视觉不进入当前主线。
- 摄像头代码与远程拍照必须保留，不能为了简化语音直接删除。
- 默认不保存原始音频或逐句对话；只保存会话元数据。
- 用户开启记忆后才保存可查看、修改和删除的加密摘要。
- API Key、SMTP 授权码、设备密钥和 WSS 令牌不得进入 Git、文档、网页构建物或固件日志。

### 架构

- 保持单仓库。
- 控制面和实时网关保持独立进程，不能重新塞回一个进程。
- 本地继续使用 SQLite；Staging/生产使用 MySQL 8。
- 当前不引入 Redis、MQTT 或微服务。
- 当前模型链保持 Qwen 实时 ASR → DeepSeek 流式 LLM → Qwen 实时 TTS。
- 用户只能选择产品化模型/音色预设，不能看到 Provider URL 或 Key。

### 固件、显示和交互

- 当前金样机保持 320×240 横屏；官网回退版继续独立保留。
- 官网版和自有版必须使用不同固件名、Bootstrap 与 OTA 发布环，禁止互刷。
- 普通应用/表情更新不得擦除 NVS、Wi-Fi、设备身份、分区表或语音模型。
- 唤醒词保持“你好小灿”。
- 播放必须使用 `start → ready → PCM → stop → drained`。
- `speaking` 只能由第一块真实扬声器 PCM 触发。
- `turn_id + reply_id` 不匹配的旧消息必须忽略。
- 回复结束后保持约 0.8 秒收尾，再聆听 10 秒，之后软睡眠。
- 当前接受的嘴型是细线 U 形五档；不要恢复白色圆环、实心椭圆或“香肠嘴”。
- 嘴型读取真实 PCM 包络；不要让 LLM 猜口型。
- TFT 尺寸、方向和资源由 Display Profile 管理，不应散落写死到业务代码。

### Git 与发布

- 只在 `E:\HENSUN_STABILITY_WT` 开发。
- 保留现有脏工作树，禁止 `git reset --hard`、`git checkout -- .` 和清理未跟踪目录。
- 每个提交只包含一个经过验证的逻辑单元。
- 未经用户明确要求，不推送、不部署、不创建 PR、不改 DNS/Cloudflare。
- 本地金样机门槛通过前，不恢复 Staging 发布。

## 5. 遇到过的问题及解决方式

| 问题 | 根因 | 已采用的解决方式 | 当前状态 |
|---|---|---|---|
| Windows 搜不到 ESP32 | 旧 COM4/COM5 记录是设备不在场，不等于驱动丢失；部分线材只供电 | 安装/保留 CH340 离线包，使用数据线，确认 `VID_1A86&PID_7523` 和当前 COM6 | 可用，COM 号仍需每次重查 |
| 首次唤醒后漏掉开头 | 连接或令牌刷新期间没有保留最初音频 | 唤醒时提前缓冲 Opus，优先复用现有 WSS/令牌 | 已有修复，需 30 轮验证 |
| ASR 等十几秒或完全沉默 | 裸 Opus 被当作完整 Opus 流；实时服务拒绝后备用链路曾关闭 | 增量封装 Ogg-Opus；实时失败时使用同一轮内存音频批量降级 | 相关测试通过，长稳待验 |
| TTS 漏字、窜字、断音 | 网关发送速度远快于真实播放，设备队列溢出；音频可能早于状态准备 | 60ms 匀速发送、启动缓冲、背压、同一回复复用编码器、ready/drained 握手 | 基础体验通过，30 轮待验 |
| 表情先于声音 | 收到 `tts.start` 就显示说话 | `tts.start` 只准备解码；首块真实 PCM 才进入 speaking | 已修复 |
| 有说话脸但没有声音 | 播放状态与音频队列脱节，旧回合消息串入 | `turn_id + reply_id`、drained、超时清理与旧消息忽略 | 已修复，长稳待验 |
| 回复后停在说话脸 | 服务端 `tts.stop` 被误当成真实播放结束，或 drained 丢失 | 本地队列真正排空后才 drained；匹配确认后进入收尾/聆听 | 已修复，长稳待验 |
| 旧表情插队、动画不连贯 | 表情请求排队、回复中途反复重启动画 | 容量 1 mailbox、`xQueueOverwrite`、generation 丢弃旧请求；情绪更新不重启整脸 | 用户已确认当前效果 |
| 嘴型像圆环、香肠或随机跳 | 五种嘴型形状差异过大，且整脸资源和口型耦合 | 改为细线 U 形五档 PCM 叠加层，20 FPS 平滑移动 | 用户已确认 |
| `mmap has no file` | `talk_neutral.eaf` 正好填满 16 字节索引槽，没有结尾 NUL | 内部名称改为 `talk_base.eaf`；测试强制每个槽含 NUL | 已修复 |
| 用户还没说下一句就睡眠 | 继续聆听只有 3 秒 | 当前改为 10 秒，无人讲话后再软睡眠 | 已确认方向 |
| 公网版迟钝、无声、状态不同步 | Staging 网络与服务链路放大了本地尚未收口的问题 | 暂停公网验证，回到本地三进程做金样机 | 当前主线 |
| TFT 偶发黑屏/冻结 | 曾涉及资源缺失、旧请求积压、显示任务与状态错乱；仍需长稳排除剩余问题 | 修复资源索引和队列；故障时先看串口，不直接擦除 Flash | 基础运行正常，8 小时待机未验收 |

## 6. 目前未完成的问题

### 必须完成后才能标记本地金样机稳定

- 五个实现提交、交接文档和评审债收口已经完成。剩余未跟踪内容仅限明确排除的 `design/` 与设备配置迁移。
- 后端测试当前为 106 项，约 105 过、1 skip（`test_qwen_realtime_integration.py` 实网集成）。
- 用户已确认当前金样机可以对话；说「小灿闭嘴」或「闭嘴」会进入软待机。
- 默认 `DEVICE_WS_URL` 已改为 `ws://127.0.0.1:8001/v1/device/ws`。不要读取或改写本机 `.env`。金样机只用 `scripts/start_local_pilot.ps1`，不要用只起控制面的 `scripts/start_lan_backend.ps1`。
- 产品决定：退出词保持现状，含单独「闭嘴」「退出」的子串匹配。已知可能误伤普通句子，暂不收紧。
- 实时 ASR 根因已确认：`server_vad` 下发送 `input_audio_buffer.commit` 会触发 `invalid_request_error`。已改为 Manual（`turn_detection: null`），本地停说时再 commit。Ogg-Opus 封装未改。batch fallback 仍保留作降级；真机是否不再 fallback 要等授权后口测确认。
- 尚未完成 30 轮固定真机回归。
- 尚未完成 2 小时连续对话运行。
- 尚未完成 8 小时待机与 TFT 冻结检查。
- 尚未形成最终验收报告，包括唤醒、STT、首段音频、drained、丢包、重连、内存和看门狗证据。
- 真机仍运行 10:56 金样机应用，不是当前本地构建。
- 本地 GitHub Actions 已覆盖 landscape 金样机变体和 `test_hensun_dialogue_faces.py`；远端 CI 要等用户明确要求推送后才会运行。

### 已规划但当前不要做

- 其余七类独立真机说话脸。
- MCP 联网搜索扩展。
- 声纹识别。
- 微信小程序。
- 支付和会员。
- 家庭模式公开使用。
- 云端视觉问答。
- 5 台内部版、20 台免费内测和量产硬件。
- 恢复 Staging 或正式域名部署。

## 7. 下一步最应该做什么

未得到用户明确口头授权前：不要刷 Flash、不要开始 30 轮口测、不要恢复 Staging 或正式域。下面只是授权后的清单，不是现在就要做。

### 授权后第一件事：刷入当前代码固件并做真机验收

当前工作树已有本地固件构建，但仍不要自动刷机。授权后先重新确认 COM 口，只写应用 `0x20000` 和表情 `0xB00000`。

当前未刷入构建：

- 固件名：`hensun-cam-selfhosted-landscape-local-v1`
- `xiaozhi.bin` SHA256 `C53A6A51ACEF8CBDDFA3EA0F3ADA8EA3D98B8F372270BB7FAEF4157A14E115B6`，2699312 / 4128768 字节
- `emote_gen.bin` 仍为金样机哈希 `4A0E2C6B1A42B620BF615FD99ED2F86EB6C0BB3146482E07C4844126A7D5AF85`，2837200 / 5242880 字节
- 真机仍运行 10:56 金样机 `45A4F6FFB20BCE4304E547D280D3B92C7CEF4A94E33713D48DCCE75A73FC6192`

### 授权后第二件事：完成真机验收

按固定矩阵完成：

1. 十次“你好小灿 + 问题”，至少九次一次成功。
2. 十次唤醒后停顿再提问，不漏开头。
3. 十轮无需重复唤醒的连续对话。
4. 短句、长句、数字、英文、时间、天气。
5. BOOT 打断和“小灿闭嘴”。
6. 回复后 0.8 秒收尾、10 秒继续聆听、自动睡眠和再次唤醒。
7. 累计 30 轮无沉默、断音、旧音频、无声说话脸或 TFT 卡死。
8. 连续运行 2 小时，再待机 8 小时。

### 通过后的顺序

只有上述门槛通过，才允许：标记金样机版本 → 推送并跑远端 CI → 恢复 Staging → 5 台内部验证 → 扩展其余对话表情。

## 8. 重要路径、启动命令和测试命令

### 必读文件

| 文件 | 用途 |
|---|---|
| `E:\HENSUN_STABILITY_WT\PROJECT_HANDOFF.md` | 本交接说明，新账号首读 |
| `E:\HENSUN_STABILITY_WT\docs\START_HERE.md` | 项目结构与当前事实 |
| `E:\HENSUN_STABILITY_WT\docs\handoffs\2026-08-18-local-golden-sample.md` | 当前脏工作树、证据和下一步 |
| `E:\HENSUN_STABILITY_WT\docs\handoffs\CHAT_CONTEXT.md` | 去噪后的历史决策时间线 |
| `E:\HENSUN_STABILITY_WT\docs\runbooks\local-golden-sample.md` | 启动、构建、刷机和恢复细节 |
| `E:\HENSUN_STABILITY_WT\docs\specs\architecture.md` | 系统架构 |
| `E:\HENSUN_STABILITY_WT\CONTEXT.md` | 产品领域语言 |
| `E:\HENSUN_STABILITY_WT\AGENTS.md` | Agent 与 Git 规则 |

### 关键代码路径

| 路径 | 职责 |
|---|---|
| `backend/app/` | 控制面 API、账户、设备、助手、配置、OTA、审计 |
| `backend/realtime/` | WSS、Provider、语音回合、播放握手、工具、表情控制 |
| `web/` | Next.js 客户控制台与内部后台 |
| `firmware/xiaozhi-esp32/main/application.*` | 固件对话与状态主入口 |
| `firmware/xiaozhi-esp32/main/audio/` | 采集、播放、唤醒与队列 |
| `firmware/xiaozhi-esp32/main/protocols/` | WSS 消息和播放确认 |
| `firmware/xiaozhi-esp32/main/boards/hensun/hensun-cam-pilot-v1/` | 板型、显示、摄像头、表情、嘴型和 Profile |
| `scripts/` | 本地启动、构建、刷机、契约、冒烟和发布门禁 |
| `migrations/` | Alembic 数据库迁移 |
| `run/` | 本机日志、PID、备份和恢复包；被 Git 忽略 |

### 新账号接手后的第一组命令

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list
```

预期分支是 `feature/hensun-stability-quality`。如果不一致，先停止，不要修改文件。

### 启动和停止本地系统

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
.\scripts\start_local_pilot.ps1 -HostAddress '192.168.5.49'
```

不打开浏览器：

```powershell
.\scripts\start_local_pilot.ps1 -NoBrowser
```

金样机只用上面的三进程启动脚本，不要用 `scripts/start_lan_backend.ps1`。后者只起控制面，设备 WS 地址也不对。

健康检查：

```powershell
Invoke-WebRequest http://127.0.0.1:3000 -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/health/ready -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8001/health/ready -UseBasicParsing
```

停止：

```powershell
.\scripts\stop_local_pilot.ps1
```

### 后端、契约和发布门禁

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
$py = .\.venv\Scripts\python.exe

& $py -m ruff check backend scripts
& $py scripts\generate_device_contracts.py --check
& $py -m pytest backend/tests -q
& $py scripts\release_gate.py
```

### 前端检查

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT\web'
npm ci
npm run lint
npx tsc --noEmit
npm run build
```

Playwright/Axe 需要按 `web/playwright.config.ts` 的本地依赖启动后再运行：

```powershell
npm run test:e2e
```

### Hensun 固件主机测试

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
$py = .\.venv\Scripts\python.exe

& $py -m unittest discover `
  -s firmware/xiaozhi-esp32/scripts/tests `
  -p 'test_hensun*.py'
```

### 构建当前横屏本地固件

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
.\scripts\build_firmware.ps1 `
  -Variant local `
  -BootstrapUrl 'http://192.168.5.49:8000/v1/device/xiaozhi-bootstrap'
```

构建必须使用 ESP-IDF 6.0.2，并确认应用、语音模型和 `emote_gen` 均未超过分区。

### 刷机前检查

```powershell
Get-PnpDevice -PresentOnly |
  Where-Object FriendlyName -like '*CH340*'

python -m esptool --chip esp32s3 --port COM6 chip-id
```

如果 COM6 不存在，先重新识别端口，不要直接刷写。

### 只刷应用和表情，不擦除设备身份

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

如果自动下载失败：按住 BOOT，轻按 RST，开始写入后松开 BOOT。不要执行整片擦除。

### 恢复已确认金样机

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

### 本地配置与敏感文件

- 根目录 `.env`：本地后端配置，禁止提交。
- `web/.env.local`：本地前端配置，禁止提交。
- `hensun-lan.db`：当前本地数据库，修改前先备份。
- `run/local-pilot/processes.json`：受控进程 PID 和日志路径。
- 不要把任何 Key、SMTP 授权码、设备密钥或令牌复制到新账号聊天；应在新环境中从受控 Secret 存储重新配置。

## 接手判定

新 Agent 如果只读一份文件，先读本文；准备改代码前，必须继续阅读 `AGENTS.md`、`docs/START_HERE.md` 和 `docs/handoffs/2026-08-18-local-golden-sample.md`，再现场运行 `git status` 和测试。任何与本文不一致的实时状态，以现场只读检查为准，不得默默覆盖现有工作。
