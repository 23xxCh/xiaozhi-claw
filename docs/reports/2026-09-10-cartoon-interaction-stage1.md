# 活泼卡通交互：第一阶段交付状态（2026-09-10）

## 已完成、未部署

- d5f0ee9：NOCAM 播放期间关闭本地唤醒检测，与 CAM 半双工保护一致。动态编译原生产条件分支：修改前 NOCAM 断言失败；修复后两板型通过，相关固件检查共 74 项通过。这是确定的分支遗漏，尚未证明就是全部第二轮无回复的根因。
- 4476891：托管路线仅对独立、明确的表情请求选择情绪，播放开始不再覆盖选定情绪。否定命令及描述自身心情不触发。S2S 与表情解析 35 项测试和 Ruff 通过。该后端变更尚未重启加载。
- NOCAM local 固件 ESP-IDF 构建成功，2792944 bytes，SHA256 3e1d87f8c6cceb81c2eae17f52e94cf0e661db738fd2710f44d49a0d821cf6ac。入口 http://192.168.1.20:8000/v1/ota/。仅固件单点修复用于首轮验证，表情后端另行部署。

## 烧录阻断

COM6 read-mac 确认 7c:4f:ad:2b:51:a8。对 ota0 的 460800 和 115200 备份读取均报 Corrupt data（期望 4096 字节，分别收到 4084 / 4042 字节）。run/backups/20260910-cartoon-stage1 内文件是失败读取产物，不能用作回退备份。未执行 write-flash；旧固件仍在设备。备份操作发生硬复位，不能把这一段算入不中断真机长稳。

## 后续执行顺序

恢复 USB 可靠传输后重新完整备份、记录哈希，只刷应用分区并验证写入；先做唤醒、BOOT、连续追问和播放中自身唤醒检查。若第二轮仍失败，继续对照 VAD、PCM 电平及阿里 SpeechStarted/SpeechContent，不放行全交互。

此后完成状态与情绪分离、PCM 嘴型、1.5 秒表情保持、约 200 ms 过渡、两行字幕和分原因反馈。上述视觉工作尚未实现，不能把明确表情请求支持称为整套计划完成。固定最终版本后重计长稳并录制对比视频，实听尾字与首响仍需真机验收。

## 重插 USB 后部署

重新确认 COM6 / MAC 7c:4f:ad:2b:51:a8。完整备份 ota0 到 run/backups/20260910-cartoon-stage1/ota0-reconnected.bin，4128768 bytes，SHA256 7e39689e0b6c3b702c4acbddf0e035e2120b5175e02d974c09714c364d600ee7。此前两份失败读取文件仍不能作为备份。

只向 0x20000 写入上述 2792944-byte 候选固件，esptool 返回 Hash of data verified 后硬复位。NVS、资产及分区表未写入。后端表情请求代码仍未加载，先单独验收播放自唤醒保护。连续追问及真机长稳尚未通过。

## 撤回前次上线判断：启动地址配置错误

首个候选使用 /v1/ota，设备实际启动接口是 /v1/device/xiaozhi-bootstrap。前次刷入校验虽成功，但启动失败并报 404，不能视为部署成功；当时使用未分时的历史 WebSocket accepted 判断重连是不充分证据。前次 soak-start 不作为新固件稳定性证明。已重新构建正确入口，后续以新启动串口和连接验证恢复。完整 ota0-reconnected.bin 仍是更新前有效回退备份。

## 正确入口修正版

CONFIG_OTA_URL 已核实为 http://192.168.1.20:8000/v1/device/xiaozhi-bootstrap。新应用 SHA256 1eaf0fd0ab1c098e180dbd386de7c45e8947dba4b052047429f37ffebb106b73，0x20000 写入 Hash of data verified。复位后新请求 192.168.1.21:49350 POST /v1/device/xiaozhi-bootstrap 返回 200，随后 :49351 WebSocket accepted/connection open。启动入口已恢复，仍不代表第二轮及整个交互计划已通过。
