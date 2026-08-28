# 本地金样机 10 秒多轮对话

## 目标

设备在一次唤醒后完成回复，播放真正排空后自动重新聆听 10 秒；用户可直接继续提问，无需再次说唤醒词。无人讲话时静默回到睡眠。

## 输入与输出

- 输入：首次本地唤醒词，以及续聊窗口内的普通语音。
- 输出：同一 WebSocket 会话中的连续语音回复；每轮继续使用匹配的 `turn_id + reply_id + drained`。

## 数据流

```text
wake -> listening -> reply pending -> real PCM speaking -> drained
     -> 0.8s settle / echo guard -> listening (10s)
     -> speech: next turn | silence: sleep
```

## 边界

- ESP32-S3 CAM 为半双工；播放期间不开麦、不检测唤醒词。
- 10 秒从麦克风真正重新开始采集后计算；检测到讲话后改用 VAD 结束本句。
- 只修改本地横屏 variant 和固件状态控制，不修改 REST、数据库、表情资源或公网环境。
