# Hensun CAM 双固件设计

## 目标

同一块 ESP32-S3 CAM 保留摄像头、ST7789 屏幕、原创表情和按键，提供互不混用的发布通道：

- `hensun-cam-official-v1`：240×320 纵屏，连接小智官方 bootstrap，由用户在小智官网管理智能体。
- `hensun-cam-selfhosted-v1`：Hensun 自有服务通用版，由服务端选择 ASR、TTS 和 LLM。
- `hensun-cam-selfhosted-landscape-local-v1`：320×240 横屏本地金样机，Bootstrap 显式指向开发电脑。
- `hensun-cam-emote-lab-v1`：表情实验版，只用于独立视觉验证，不作为客户固件。

当前横屏资源有 12 个系统/展示状态；后端支持 10 类对话表情标签，但真机当前只完成 `neutral/happy/caring` 三套独立说话脸。两者不能写成“已经完成 60 套运行时表情”。

## 接口设计

### 固件构建

- 输入：`official`、`selfhosted` 或 `local`；自有版额外输入 bootstrap URL。
- 输出：带有独立 OTA 报告名称的合并固件包。
- 不变量：GPIO、摄像头和音频采样率保持一致；显示方向和表情资源由 Display Profile/产品变体选择。
- 本地金样机命令：`build_firmware.ps1 -Variant local -BootstrapUrl <LAN URL>`。

### 自有模型服务

- ASR 输入：ESP32 上行的 16 kHz、单声道、60 ms Opus 帧；服务端封装为 Ogg Opus 后上传。
- ASR 输出：包含 `text` 的 JSON。
- LLM 输入：OpenAI Chat Completions 兼容 JSON；输出 assistant 文本。
- TTS 输入：OpenAI Speech 兼容 JSON；输出任意 FFmpeg 可识别的音频。
- TTS 输出：服务端统一转为 24 kHz、单声道、60 ms Opus 帧后下发。

播放使用 `tts.start → ready → PCM → stop → drained`，并以 `turn_id + reply_id` 关联。`tts.start` 只准备解码器；第一块真正送入扬声器的 PCM 才能触发说话表情。

所有 URL、模型名和 API Key 只存在于服务端环境变量中，设备固件和客户页面不得接触 API Key。

## 数据流

```text
官网版:    ESP32 -> 小智官方 bootstrap/WS -> 小智官方模型与智能体

自有版:    ESP32 -> Hensun bootstrap/WS
                         |-> ASR_URL
                         |-> LLM_URL
                         `-> TTS_URL -> Opus 规范化 -> ESP32 扬声器
```

## 边界情况

- 官网版和自有版使用不同固件名称，禁止 OTA 互相覆盖。
- 自有版构建时未提供真实 bootstrap URL，则保留 `.invalid` 安全地址并拒绝作为现场固件使用。
- 自有模型模式缺少任一 URL、模型名或密钥时，生产配置启动失败。
- 单轮音频超过 1 MiB、空录音、ASR 空文本和模型超时均返回明确错误。
- 当前 CAM 板是单工音频；本设计不宣称全双工 AEC。
- 官网版和自有版使用独立固件名与 OTA 发布环，禁止跨版本下发。
- 普通应用/表情更新不得擦除 NVS、Wi-Fi 或设备身份。

## 影响文件

- 固件板型配置：`firmware/xiaozhi-esp32/main/boards/hensun/hensun-cam-pilot-v1/config.json`
- 显示 Profile：`firmware/xiaozhi-esp32/main/boards/hensun/hensun-cam-pilot-v1/display_profiles.json`
- 构建和烧录入口：`scripts/build_firmware.ps1`、`scripts/flash_firmware.ps1`
- 模型配置：`backend/app/config.py`
- Provider 适配器：`backend/realtime/providers.py`
- 实时语音与播放协调：`backend/realtime/session.py`
- 对话表情控制标记：`backend/realtime/face_control.py`
- 固件播放/状态入口：`firmware/xiaozhi-esp32/main/application.cc`、`main/audio/audio_service.cc`、`main/protocols/protocol.cc`
