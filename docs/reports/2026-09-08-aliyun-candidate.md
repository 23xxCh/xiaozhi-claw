# 阿里托管语音候选验证记录

日期：2026-09-08。结论：软件候选通过本批次检查，日常入口未启用，未烧录新固件。

## 已完成

- 阿里适配器、候选目录、网页能力提示、兼容音色和原子切换。
- 协议及网关模拟、原语音路线/豆包/发布回归通过；新协议测试覆盖等待 Listening、
  重复判停和识别、取消后事件丢弃、错误脱敏、目录门禁和独立供应商用量。
- 网页 3 个 Playwright 用例通过，包含阿里切换与原设置恢复；TypeScript、相关 ESLint
  和 Ruff 检查通过。
- 既有双进程 WSS smoke 通过：2 进程、1 轮、ready/drained 和撤权 Outbox。
  此项使用 mock 音频，不能冒充双进程真实阿里音频验收。

## 真实供应商 + 模拟设备

复用获授权的合成测试 WAV（3.985 秒、16kHz 单声道 int16），通过现有诊断脚本：

```powershell
.venv/Scripts/python.exe scripts/doubao_gateway_probe.py --provider aliyun-dialog --input-wav run/doubao/synthetic-input.wav --output run/doubao/aliyun-gateway-fixed-20260908.json
```

首次网关联调失败：客户端用户标识超过阿里长度限制。改为 32 字符 UUID hex 并增加
测试约束后复测通过。失败记录保留，不从样本中删除。

复测：一次上游连接；67 个设备输入 Opus 包；50 个输出 Opus 包；24kHz 解码得到
144000 字节 PCM；1 个完成轮次；ready、drained 和 speaker 回执均由模拟设备发送。
停止上传到首个网关音频包 2625ms。单样本不能算 P50/P90，也不是实际听见声音延迟。
用量 provider 为 aliyun-dialog，operation 为 managed_dialog，成本 unknown。

元数据位于忽略目录 `run/doubao/aliyun-gateway-fixed-20260908.json`；历史脚本名保留
以兼容既有命令，新增 --provider 明确选择供应商。真实测试不会自动尝试其他路线。

## 待放行项

1. 使用真实设备完成收音、喇叭实听、首尾音、表情和连续交互；目前每轮独立，无历史。
2. 真机取消、解绑、断网恢复；长句、空输入、超时和错误恢复。
3. 人设模板参数、历史传递、云端工具和数据设置的明确契约及验证。
4. 相同配置与题集的多样本延迟/成功率及实际账单对照。

设置与回退见 ADR 0007。当前应用候选不满足“完整多轮产品已可用”的结论。
