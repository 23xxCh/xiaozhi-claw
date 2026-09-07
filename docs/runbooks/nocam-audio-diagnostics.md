# NOCAM 音频与表情验收

适用首版：`hensun-nocam-pilot-v1`、ESP32-S3、Wi-Fi、轮流说话。
硬件输入与实际听感必须由现场人员或经过校准的音频治具验收，不能用源码检查、
编译成功、WebSocket 返回成功代替。此文不授权刷机、打开串口或改接线。

## 当前证据与能力

2026-09-07 只读 Windows 设备枚举发现 `USB-SERIAL CH340 (COM6)`，PnP 状态 `OK`。
本轮没有打开串口、读取实时麦克风、播放扬声器、复位或刷入固件。
串口适配器在线不能证明它连接的板型、板上应用版本或麦克风正常。

最新找到的 2026-09-03 工厂 QC 记录为 `failed-voice-wake`：有输入电平，三次现场唤醒
没有检测事件；显示、扬声器、按键仍待人检查，三轮对话被唤醒失败阻塞。
该记录早于本次修改，只作为待复验项目，不能代表新代码的真机结果。

| 能力 | NOCAM 当前源码 | 验收含义 |
| --- | --- | --- |
| `strict_playback_ack` | 宣告支持，复用公共 ready/drained 实现 | 要看对应 reply_id 的确认，不以兼容超时算成功 |
| `device_stage_telemetry` | 宣告支持，补齐 capture_started / speaker_pcm_started / playback_drained | 是固件阶段事件，不能替代音频测量 |
| `pcm_mouth_sync` | 明确 false | 当前是 GIF 状态/情绪切换，不支持随 PCM 音量变化的口型 |
| 下行播放 | 24k 单声道、60ms 裸 Opus → PCM → I2S | 仍须现场听到正确完整语音 |
| 上行采集 | 16k 单声道、60ms 裸 Opus | 麦克风输入质量仍须实物复验 |

`speaker_pcm_started` 当前表示 PCM 即将交给音频 codec，发生在 I2S 写入前。
`playback_drained` 表示软件解码/播放队列及在途任务排空；它没有单独测量喇叭的声学尾音。
CAM 具有 PCM 口型的历史结果不能作为 NOCAM 表情通过的证据。

## 最小诊断顺序

1. **锁定被测组合。** 记录板型、应用版本/哈希、资源版本、麦克风与功放实际型号、
   接线照片、供电及测试环境。NOCAM 当前配置是麦克风 WS=4/SCK=5/DIN=6，
   功放 DOUT=7/BCLK=15/LRCK=16；不要通过改 CAM 的引脚冒充新板型。
2. **先确认输入信号。** 现场分别采集安静、正常说话、背景噪声三个条件；先看
   I2S 左右槽、24 位在 32 位槽中的对齐、16k 采样率、是否长期为零、削顶及底噪。
   当前换算是 `>>16` 后 4 倍增益。保留 avg_abs、peak、削顶比例、时长、距离等
   数值，不能仅凭“有电平”通过。若需短 PCM 样本，先取得被测者同意，限定范围和
   删除时间；默认不保存原始音频。先核对接线/槽位和信号，再决定是否调增益。
3. **用按键隔离云端链路。** 暂时绕开唤醒，人工按键开始/结束输入，测试短句、数字、
   中英混合及长句。把“听见输入→ASR 文本正确→回复音频→扬声器完整播出”逐项确认。
   同一轮记录上行首包、listen.stop、云端 ASR 结束、首个 TTS PCM、设备 ready、
   网关首包、设备 PCM 提交、drained；没有某阶段时在该处停止扩展功能。
4. **再测唤醒与 VAD。** 在已通过的输入链上测试不同说话者、距离、角度和噪声，
   分别记录检测率、误唤醒及首字完整性。当前 NOCAM 的 VAD 静音尾部为 100ms，
   必测句中停顿会不会误结束；每次只调整一个参数，不把增益/VAD/模型同时换掉。
5. **测播完续聊、表情和故障。** 连续三轮起步，确认实际听感无丢字、串音、爆音；
   检查 GIF 等待/说话/待机状态、取消后无旧音频、网络中断后恢复、播完再开始收音。
   NOCAM PCM 口型维持“不支持”，不能写成“嘴型通过”。通过后再进入
   [现有完整门槛](pilot-acceptance.md)：两批各 50 次尝试、成功率与延迟门槛、
   两小时对话及八小时待机；完整分母包括失败和回退。

每项填写 `pass / fail / pending-human-check`、证据位置和被测版本。输入、唤醒或
实际扬声器任一关键项失败，工厂结果不得标为完成三轮对话或整机合格。

## 服务端音频桥的本地验证范围

`StreamingOpusToPcm` 使用既有 FFmpeg，把设备 60ms 裸 Opus 包包装成连续 Ogg 输入，
再输出 16k 单声道 `s16le` 的 640 字节/20ms PCM 块。原始 Opus 包不携带容器
pre-skip 信息，且设备编码器跨轮次持续存在，所以该桥显式使用 `pre_skip_samples=0`，
不在每一轮猜测并删除 104 个输入样本；阿里既有封装器的默认参数保持不变。
编解码器自身的启动预测状态和延迟仍须实际短句验收。

API：`start()` → 并行消费 `chunks()` 与调用 `write(packet)` → `finish()` 排空 →
在 `finally` 中 `cancel()` 回收。输出队列最多 50 块（1 秒）；慢消费者会产生背压。
不要在设备 WebSocket 唯一接收循环里等待 PCM 消费结束，否则会阻碍取消等控制消息。
`finish()` 不补静音，不推测豆包的轮次结束事件。豆包适配器自行确定提交操作。
Ogg/Opus 容器也不能直接当成设备需要的裸 Opus 下发。

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_streaming_opus_decoder.py backend/tests/test_realtime_media.py backend/tests/test_audio_formats.py -q
.\.venv\Scripts\python.exe -m unittest discover -s firmware/xiaozhi-esp32/scripts/tests -p 'test_hensun_*.py' -q
```

音频桥测试使用内存中的合成信号和本地 FFmpeg，检查完整采样数、首尾信号、
EOF 前输出、满队列取消、消费者取消和非法 Opus 回收；不发送付费请求、不访问真机。
固件 host tests 主要检查资源与源代码合同，不执行板上的驱动/显示/音频任务。
