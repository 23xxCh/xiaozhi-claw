# Hensun CAM 双固件设计

## 目标

同一块 ESP32-S3 CAM 保留摄像头、240x320 屏幕、36 状态表情和按键，提供两套互不混用的固件：

- `hensun-cam-official-v1`：连接小智官方 bootstrap，由用户在小智官网管理智能体。
- `hensun-cam-selfhosted-v1`：连接 Hensun bootstrap，由 Hensun 服务端选择 ASR、TTS 和 LLM。

## 接口设计

### 固件构建

- 输入：`official` 或 `selfhosted`；自有版额外输入 bootstrap URL。
- 输出：带有独立 OTA 报告名称的合并固件包。
- 不变量：GPIO、摄像头、表情资源、唤醒词和音频采样率保持一致。

### 自有模型服务

- ASR 输入：ESP32 上行的 16 kHz、单声道、60 ms Opus 帧；服务端封装为 Ogg Opus 后上传。
- ASR 输出：包含 `text` 的 JSON。
- LLM 输入：OpenAI Chat Completions 兼容 JSON；输出 assistant 文本。
- TTS 输入：OpenAI Speech 兼容 JSON；输出任意 FFmpeg 可识别的音频。
- TTS 输出：服务端统一转为 24 kHz、单声道、60 ms Opus 帧后下发。

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

## 影响文件

- 固件板型配置：`firmware/xiaozhi-esp32/main/boards/hensun/hensun-cam-pilot-v1/config.json`
- 构建和烧录入口：`scripts/build_firmware.ps1`、`scripts/flash_firmware.ps1`
- 模型配置和适配器：`backend/app/config.py`、`backend/app/providers.py`
- Opus/Ogg 转换：`backend/app/audio_formats.py`
- 设备语音循环：`backend/app/routers/device_ws.py`

