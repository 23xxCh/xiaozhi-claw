# 非 CAM 自定义离线唤醒词

## 目标

让 `hensun-nocam-selfhosted-v1` 在设备本地使用“你好小灿”作为主唤醒词，并保留“小灿”作为辅助唤醒词，不上传唤醒音频，也不改变其他板型。

## 接口设计

- 输入：16 kHz 单声道麦克风音频，以及编译期拼音命令 `ni hao xiao can` / `xiao can`。
- 输出：本地 MultiNet 命中后分别上报显示文本“你好小灿”或“小灿”，继续走现有唤醒事件链路。

## 数据流

`麦克风 -> AFE/VAD -> MultiNet5 命令识别 -> 现有唤醒回调 -> Staging 实时会话`

## 与现有代码的关系

- 只在非 CAM self-hosted 构建配置中启用 `USE_CUSTOM_WAKE_WORD` 与中文 MultiNet5 Q8 模型。
- 阈值从 15 开始，真实设备刷入后再验证漏唤醒与误唤醒。
- 保持 `SEND_WAKE_WORD_DATA=n`，唤醒识别离线完成。
- 编译和产物检查不等于麦克风实机识别；后者必须在用户另行确认刷机后完成。
