---
status: in-progress
branch: feature/hensun-stability-quality
timestamp: 2026-08-28T21:00:00+08:00
worktree: E:\HENSUN_STABILITY_WT
base_head: 41439eea809412d43603bd62142cd06ff4825ba8
---

# Hensun 本地金样机交接记录

## 一句话状态

当前 ESP32-S3 CAM 已恢复本地多轮语音；横屏说话脸、细线嘴型和「小灿闭嘴 / 闭嘴」软待机已得到用户确认。后端 133 passed、1 skipped，固件主机/Profile/资源测试 121 passed，前端门禁和 ESP-IDF 三版本构建通过。稳定性实现和设备配置迁移已拆成本地提交，工作树只剩明确排除的 `design/`。30 轮真机回归、2 小时连续对话和 8 小时待机尚未做；不要把基础可聊天写成金样机已稳定。

## 接手前必读

- [项目入口](../START_HERE.md)
- [聊天决策时间线](CHAT_CONTEXT.md)
- [本地金样机手册](../runbooks/local-golden-sample.md)
- [系统架构](../specs/architecture.md)
- [产品领域语言](../../CONTEXT.md)
- [Agent 与 Git 规则](../../AGENTS.md)

## 用户当前目标

用户要求先把本地核心体验做好，不再忍受以下问题：

- 说完以后等待十几秒或二十秒。
- ASR 偶发无结果，需要说两三次。
- TTS 漏字、窜字、断音或上一轮串入下一轮。
- 表情早于声音、无声时仍显示说话、回复结束后卡在说话脸。
- TFT 黑屏、冻结、旧表情插队或整张脸不连贯。
- 修改接口、TFT 尺寸或参数时牵动大量文件。

最新一轮针对“嘴型像香肠、脸部过渡不连贯”进行了修正。用户在真机上回复“可以了”，随后要求巩固成果并写清项目与聊天上下文。

## 已锁定的产品与技术决定

### 产品

- 当前公开边界是 18+ 成人桌面 AI 助理与轻陪伴。
- 家庭模式、支付、微信小程序、声纹和云端视觉不进入当前本地稳定主线。
- 摄像头代码与远程拍照保留，不为简化语音而删除。
- 退出词保持现状，含单独「闭嘴」「退出」的子串匹配。已知可能误伤普通句子，暂不收紧。

### 开发环境

- 只在 `E:\HENSUN_STABILITY_WT` 开发。
- 当前分支是 `feature/hensun-stability-quality`。
- 禁止直接修改 `E:\AI TOY\xiaozhi-claw`，也不要清理其他 worktree。
- 不自动推送、部署或创建 PR；用户明确要求后再做。

### 运行架构

- 前端 `3000`、控制面 `8000`、实时网关 `8001`，保持三个进程。
- 默认 `DEVICE_WS_URL` 是 `ws://127.0.0.1:8001/v1/device/ws`。不要读取或改写本机 `.env`。
- 金样机只用 `scripts/start_local_pilot.ps1`，不要用只起控制面的 `scripts/start_lan_backend.ps1`。
- 本地数据库是根目录 `hensun-lan.db`；脚本启动前会备份并迁移。
- 当前模型链路保持 Qwen 实时 ASR → DeepSeek 流式 LLM → Qwen 实时 TTS。
- 实时 ASR 失败时才使用同一轮内存音频执行批量 Qwen ASR 降级。
- 不保存原始音频或逐句对话。

### 固件与交互

- 当前金样机固件是 `hensun-cam-selfhosted-landscape-local-v1`。
- 屏幕是 ST7789，逻辑 320×240 横屏；纵屏官网版保留为独立回退。
- 唤醒词是“你好小灿”；BOOT 待机时开始聊天，活动时停止并软待机。
- 回复播放采用 `tts.start → ready → PCM → stop → drained`。
- `turn_id + reply_id` 用于拒绝旧轮次 ACK、旧情绪和旧停止事件。
- 第一块真实扬声器 PCM 才能触发说话脸和口型。
- 回复结束后显示约 0.8 秒收尾并经过余响保护，再开放 10 秒续聊；无人讲话后静默进入睡眠表情。

### 表情与嘴型

- 后端解析十类 `[[face:emotion]]` 标签，每轮最多接受两次，并从 TTS/正文/日志剥离。
- 当前表情包包含 12 个系统/展示状态和 3 个独立运行时说话脸：`talk_base`、`talk_happy`、`talk_caring`。
- 其余七类对话标签当前没有全部成为独立真机说话资产，不得在文档或演示中宣称已经完成十套真机脸。
- 嘴型是叠加层，不制作“情绪数 × 嘴型数”的完整 GIF 笛卡尔积。
- 当前接受的嘴型是细线 U 形五档，20 FPS 平滑移动；禁止恢复白色圆环、实心椭圆和随机跳变。
- 表情队列长度为 1，只保留最新请求；generation 用来丢弃旧切换。

## 当前运行状态

2026-08-19 读取到：

- `http://127.0.0.1:3000`：HTTP 200。
- `http://127.0.0.1:8000/health/ready`：HTTP 200。
- `http://127.0.0.1:8001/health/ready`：HTTP 200。
- `http://192.168.5.49:8000/health/ready` 与 `8001` 也是 HTTP 200。
- PID 与日志由 `run/local-pilot/processes.json` 管理；刷机时发现旧 PID 已死，且 `127.0.0.1:8000` 被无关项目占用，已按命令行确认后结束并重启 hensun 三进程。
- 设备通过 CH340 `COM6` 连接；MAC 为 `28:84:85:4a:3d:b8`。

端口、PID、COM 编号和设备 IP 都可能变化。下一位 Agent 必须现场重新检查，不要把以上快照当作永久配置。

## 当前 Git 状态

当前 HEAD 以 `git log -1 --oneline` 为准。本轮冻结新增提交：

```text
3b2d858 db: add versioned device config contract
93a70c7 build: align local multi-turn gates and startup
a5eb95a feat(firmware): stabilize bounded multi-turn display flow
28de5fc fix(realtime): stabilize local multi-turn recovery
```

以及本次文档冻结提交。分支没有已记录的 upstream，不要推送。

工作树现在只应剩下明确排除、不得混入的 `design/`。

禁止 `git add -A`，禁止 reset 或删除未跟踪目录。

## 本轮刚完成的修复

1. 从用户视频中确认问题不是单纯美术不好看，而是：旧动画请求积压、回复中途重启整脸、入口/循环/回归桥接不连续、嘴型家族差异过大。
2. 显示切换改成一项 mailbox，使用 `xQueueOverwrite`，旧 generation 被丢弃。
3. 回复中情绪更新只保存情绪，不反复重启整张说话动画。
4. 中性/开心/关怀运行时脸去掉导致单眼塌缩的桥接帧。
5. 嘴型统一为细线 U 形五档，并限制每 50ms 最多移动一档。
6. 打包时发现 `talk_neutral.eaf` 正好 16 字节，索引槽没有结尾 `NUL`，运行时报告文件不存在。改为内部短名 `talk_base.eaf`，并新增“每个文件名槽必须包含 NUL”的回归测试。
7. 旧测试仍硬编码查找 `QueueAnimation("speaking")`，与新的 `ConversationAnimation()` 设计不一致；已修正测试并重新跑绿。

## 验证证据

- 后端完整测试：133 passed、1 skipped（实网集成测试）。
- 实时 ASR 已从 `server_vad` + commit 改为 Manual；实时失败时保留同轮批量降级。2026-08-28 冻结没有再次刷机；当前物理应用和表情已读出到独立快照。未做用户在场 30 轮口测，不能标记金样机已稳定。
- 用户已确认当前金样机可以对话；「小灿闭嘴」和「闭嘴」可进软待机。
- 固件主机/Profile/资源测试：121 项通过。
- `scripts/release_gate.py`：PASS（仅源码/配置门禁）。
- 前端 ESLint、TypeScript 和 Next.js 生产构建通过，共 17 个页面。
- ESP-IDF 6.0.2 的本地横屏版、官网回退版和通用自有版完整构建通过；本地版应用仍有约 35% OTA 分区空间。
- 应用和 `emote_gen` 两个分区刷写均通过 esptool 哈希验证。
- 串口启动日志确认：`emote_gen` 挂载、Wi-Fi、Bootstrap、MultiNet 和“你好小灿”加载成功；不再出现 `talk_neutral.eaf` 缺失。
- 用户真机确认最新表情和对话“可以了”。

这不等于 30 轮或长稳验收已经通过。

## 可恢复金样机

当前可聊天状态冻结路径：

```text
E:\HENSUN_STABILITY_WT\run\backups\20260828-200611-current-chat-freeze
```

| 文件 | 大小 | SHA256 |
|---|---:|---|
| `xiaozhi.bin` | 2701120 | `B6551273893B988489D65C9AC3E04E109535834CD404C7315D6AA9DC2C6A94BC` |
| `emote-current-standard-shy-sad.bin` | 4659246 | `78D0F3AF53C57D6890681F28D0AAA3C1011B5DB9C520550D729873D68870878F` |

旧版回退路径：

```text
E:\HENSUN_STABILITY_WT\run\backups\golden-local-face-20260818
```

| 文件 | SHA256 |
|---|---|
| `xiaozhi.bin` | `45A4F6FFB20BCE4304E547D280D3B92C7CEF4A94E33713D48DCCE75A73FC6192` |
| `emote_gen.bin` | `4A0E2C6B1A42B620BF615FD99ED2F86EB6C0BB3146482E07C4844126A7D5AF85` |
| 固件 ZIP | `3614CF4651B7CE0A8DB91A8C6F9EC24FC869235F6B30B922DCEF05968D866A32` |

只恢复应用 `0x20000` 和表情 `0xB00000`，不要擦除 NVS、Wi-Fi、设备身份、分区表或语音模型。完整命令见 [本地金样机手册](../runbooks/local-golden-sample.md)。

## 下一步，按优先级

刷机授权已执行，串口冒烟通过。下一步必须用户在场做口测；不要恢复 Staging 或正式域。不要把串口冒烟写成金样机已稳定。

1. 不要把 `design/` 混入任何提交；设备配置迁移已经独立提交。
2. 实时 ASR 已改为 Manual；Ogg-Opus 未改。真机是否不再走 batch fallback，要等用户在场口测确认。
3. 应用 `0x20000` 和表情 `0xB00000` 已刷入当前本地构建；不要再刷，除非用户再次授权。
4. 下一步真机矩阵：先说两次「你好小灿」冒烟，再测唤醒、停顿后提问、连续对话、中性/开心/关怀、长句、数字、英文、时间、天气、BOOT 打断、“小灿闭嘴”、自动睡眠；累计 30 轮。
5. 口测通过后再做 2 小时连续运行与 8 小时待机，记录 TFT、队列、内存、重连、播放丢包和看门狗。
6. 通过后才允许标记本地金样机版本、推送远端 CI、恢复 Staging，或扩展其余对话表情。

## 不要重复尝试的错误路线

- 不要用提示词里的 `[mood:...]` 文本驱动官网版表情；官网协议本来就有单独 emotion 事件。
- 不要在 `tts.start` 时提前显示说话；那时可能还没有任何可播放音频。
- 不要把裸 ESP32 Opus 包直接声明为完整 `opus` 文件发给实时 ASR；需要正确的 Ogg-Opus 封装。
- 不要让 TTS 音频突发下发；设备播放队列会溢出并造成漏字、窜字和断音。
- 不要把 ASR 的用户情绪误当作机器人回复情绪；机器人回复情绪来自安全规则、路由和可控的主模型标记。
- 不要用全屏 GIF 组合出“十种情绪 × 五种嘴型”；嘴型应当是独立 PCM 叠加层。
- 不要仅凭亮屏或 HTTP 返回就宣称设备、配置或发布已成功；必须检查 ACK、串口或平台可见状态。

## 建议下一会话使用的技能

- `using-git-worktrees`：确认继续在当前 worktree，不污染主工作区。
- `context-restore`：从本交接记录恢复任务边界。
- `code-review`：拆分提交前审查当前脏工作树。
- `bug-fix` 或 `investigate`：若真机回归重新出现无声、延迟或 TFT 冻结。
- `finishing-a-development-branch`：只有本地验收完成且用户要求提交/推送时使用。

## 接手后的第一组命令

```powershell
Set-Location 'E:\HENSUN_STABILITY_WT'
git branch --show-current
git status --short

$py = .\.venv\Scripts\python.exe
& $py -m pytest backend/tests -q
& $py -m unittest discover `
  -s firmware/xiaozhi-esp32/scripts/tests `
  -p 'test_hensun*.py'
& $py scripts/release_gate.py
```

如果测试结果与本记录不一致，先定位差异，不要继续扩展功能。
