# 豆包候选配置、迁移与验收

日期：2026-09-07。当前是可测试候选，不是可售发布。实现与限制见 [ADR 0006](../adr/0006-selectable-voice-routes.md)，本轮结果见[候选报告](../reports/2026-09-07-doubao-candidate.md)。

## 1. 服务端配置

在服务端秘密配置中保存 `DOUBAO_API_KEY`，不要放网页、固件、Git、报告或终端参数。服务端默认值：

```dotenv
DOUBAO_REALTIME_ENABLED=false
DOUBAO_REALTIME_VALIDATED=false
DOUBAO_REALTIME_URL=wss://openspeech.bytedance.com/api/v3/duplex/realtime/dialogue
DOUBAO_API_KEY=
```

当前目录：模型预设 `doubao-realtime`，路线 `realtime_s2s`，供应商 `doubao`，模型 `1.2.6.1`；音色预设 `doubao-vv`，供应商音色 `zh_female_vv_jupiter_bigtts`。初始语速/音量使用协议默认 0。不要将该模型填入 ASR/LLM/TTS 三段字段。

控制面和网关使用一致配置。仅验证环境设置 `ENABLED=true`，并在管理目录启用候选项。生产环境启动/启用同时要求 `VALIDATED=true`，该标志只能在本手册真实放行记录完成后设置；它是运营放行记录的执行开关，不是程序自动认证。

密钥缺失、权限拒绝、超时、限流都应清晰报错；不会切到阿里路线。用户可以重试，或原子保存兼容的路线与音色后下轮使用。不要在活动播放中更改供应商连接。

## 2. 数据迁移与发布顺序

本轮新增迁移 `20260907_09`（语音路线及未知费用）与 `20260907_10`（设备首次赠送防重）。代码自动建表仅适用于全新开发/测试库，不能代替旧库 Alembic 迁移。

1. 固化当前应用和固件哈希、导出模型/音色/角色选择及权益分布；备份数据库和服务端秘密配置到受控备份位置。秘密不进入发布包。
2. 在目标数据库的隔离副本设置 `DATABASE_URL`，确认数据库地址后执行以下命令；本轮没有对当前日常/生产库执行迁移。

   ```powershell
   python -m alembic current
   python -m alembic upgrade head
   python -m alembic current
   ```

3. 期望版本为 `20260907_10`。核对旧角色 ID、路线、音色和权益未被改写；旧模型均为 cascade。启动后目录仅补入缺失候选，不自动开启豆包。
4. 核对赠送状态：明确从未激活的工厂库存为 `eligible`；曾有归属、消费过认领码、解绑次数或认领审计证据的旧设备为 `legacy-consumed`。旧记录不伪造赠送时间。有人工清零或审计丢失的库存必须运营核查后才能销售。
5. 先部署双路线后端与网页，隔离环境开启候选，完成 G2/G3。再启用生产目录，最后将豆包设为新角色默认值；旧角色保持原值。

本机实际 SQLite 存量副本已从 20260829_07 迁移到 20260907_10，旧角色选择和权益逐行一致。目标 MySQL 并发和存量副本尚未验收，本地 SQLite 测试不能代替目标 MySQL 事务验证。迁移 09 的 DDL 失败后应按数据库实际状态恢复副本，不能假设所有 MySQL DDL 自动回滚；迁移 10 对自身字段支持部分执行后的重跑。

回退优先使用上一份**仍识别双路线**且包含撤权、数据和赠送修复的候选代码，关闭豆包入口并恢复旧默认。已选择豆包的用户明确收到不可用提示并手动切换，不静默改其角色。

迁移 09 遇到 S2S 预设或未知费用拒绝删除新字段；迁移 10 遇到真实赠送记录拒绝丢掉防重字段。不要删除业务行来强迫 downgrade，不要恢复旧备份覆盖已经产生的新数据。进入写入期后采用向前修复或经核对的数据迁移。

## 3. 可重复的软件检查

在项目根目录运行，复用已有 Python、pytest、FFmpeg；网页使用已有 npm 依赖。

```powershell
python -m pytest backend/tests
python scripts/dual_process_smoke.py
python -m pytest firmware/xiaozhi-esp32/scripts/tests
python scripts/export_openapi.py
```

网页目录运行 `npm.cmd run lint`、`npx.cmd tsc --noEmit`、`npm.cmd run build`。已有 Playwright 用例覆盖角色选路/不兼容参数和离线认领。双进程 smoke 使用自己的临时库、WSS 与 mock 音频，验证 ready/drained、HTTP 解绑、旧 WSS 4403 和 Outbox delivered；不接触日常数据库或外部模型。

受控真实调用明确选择无个人数据的测试音频，会使用供应商服务：

```powershell
python scripts/doubao_probe.py --output run/doubao/session-probe.json
python scripts/doubao_probe.py --audio run/doubao/synthetic-input.wav --output run/doubao/audio-probe.json --save-audio run/doubao/synthetic-response.wav
python scripts/doubao_gateway_probe.py --input-wav run/doubao/synthetic-input.wav --offline --output run/doubao/gateway-offline.json
python scripts/doubao_gateway_probe.py --input-wav run/doubao/synthetic-input.wav --output run/doubao/gateway-live.json
```

第一条只建鉴权会话。第二条限制 15 秒、16 kHz 单声道 16 位 WAV，验证真实协议与 PCM；诊断文件位于忽略的 `run/`。需要输出录音时才加 `--save-audio`。结果不保存文字、密钥或整个原始事件。

后两条走完整网关和双向 Opus/PCM 转换，使用独立临时数据库、模拟设备回执、45秒子进程超时及有界回收；`--offline` 不调用外部供应商，省略则最多尝试一次真实连接。ASR完成事件使用豆包的 `text` 字段，不能套用其他接口的 `transcript`；delta可被后续结果修订，最终text才用于输入审核。

`first_audio_from_probe_start_ms` 包含连接和输入时长，不能用于整机首响指标。模拟设备发出的 ready/drained 或 speaker_pcm_started 也不是扬声器证据。

## 4. 放行记录

每项填写版本、环境、日期、负责人、原始结果位置和失败样本。空白等于未测。

| 门禁 | 覆盖 | 当前状态 |
|---|---|---|
| 路线契约 | 旧角色不变、双角色分别选路、当前轮冻结、下轮生效、默认来源一致 | 自动回归通过 |
| 不兼容与故障 | 音色/参数拒绝、失效密钥、取消、超时；无跨路线自动切换 | 本地协议回归通过；生产故障演练未测 |
| 真实供应商 | 建会话、合成输入、识别、文字、流式音频、usage、done | 受控普通对话通过；工具及限流等未测 |
| 音频桥 | 首尾样本、16k/24k、位深、20ms/60ms、背压与取消 | FFmpeg 自动检查通过；物理声学未测 |
| 工具 | 白名单、参数、call_id、防重复、当前权限、取消后失效 | 模拟回归通过；真实供应商工具来回未测 |
| 输出安全 | strict_audit、本地退出、文本与音频先后、取消联动 | 配置/代码检查通过；真实风险与顺序验收未测 |
| 计量 | 幂等、取消保留、未知值、用户额度分开、真实账单核对 | 本地幂等通过；价格/账单核对未测 |
| 物理收音/唤醒 | 接线、声道、位移、音量、噪声、削顶、BOOT及唤醒 | 未通过；参见 NOCAM 诊断手册 |
| 整机两路线 | 各两组50次、每组至少5会话、成功率≥98% | 未测 |
| 整机延迟 | 服务端确认输入结束至设备speaker_pcm_started，P50≤2000ms/P90≤2500ms；另测外部录音 | 未测 |
| 整机稳定 | 2小时连续对话、8小时待机，无截尾/爆音/串轮/冻结/异常重启 | 未测 |
| 开箱 | 10名新成年人中≥9人在3分钟无提示完成配网认领，各10轮 | 未测 |
| 商业故障 | 目标MySQL并发、备份恢复/删除不复活、OTA断电回退、额度到期与售后 | 部分软件修复，端到端未测 |
| 容量与试用 | 5→20→50路；5台内部后20台成年人至少7天 | 未测 |

首版轮流讲话：云端抢答、重复回复、尾音丢失、取消后旧声音或失效工具执行，任一出现都不能启用。NOCAM 的 PCM 音量口型尚缺实现，若产品保留该承诺须补实现和实测后放行。自然语音打断、海外语言/区域/支付/硬件在对应阶段另验收。

## 5. 协议参考*--

用户批准方案引用的[豆包 3.0 接口](https://docs.volcengine.com/docs/6561/2549778)、[音频约束](https://docs.volcengine.com/docs/6561/2549732)、[鉴权](https://docs.volcengine.com/docs/6561/1816214)。真实调用报告记录本次实际返回字段；未来模型/协议版本须再核实，不能只改名称后标记已验证。
