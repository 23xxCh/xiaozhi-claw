# ADR 0007：阿里多模态托管应用候选

- 日期：2026-09-08
- 状态：软件候选；未获真机或生产放行。

## 决策

新增 `managed_app` 路线，供应商 `aliyun-dialog`、模型 `multimodal-dialog`。
阿里应用内包含 ASR/LLM/TTS，并不代表原生语音到语音模型，也不伪造三个独立供应商。
沿用 `Agent.model_preset_id`、原子保存及下轮快照。经典路线和豆包不变。
`aliyun-dialog` 模型预设默认关闭且非默认，唯一兼容音色为 `aliyun-app-default`。
该音色代表应用控制台当前默认音色，不是可跨模型复用的具体音色 ID。

## 边界

本批次用于比较用户已有阿里应用与设备链路的效果。每轮新建供应商会话，使用随机
32 字符用户标识，不转发 Hensun 的人设、历史、用户标识或摘要。应用配置决定云端人格
和能力；不会执行供应商返回的设备命令。云端插件仍须由应用管理者配置与核验。
这不是多轮记忆产品的最终实现：应用模板参数及历史传入需另行验证后开放。

能力接口增加 `system_prompt` 和 `history`，旧路线默认均为 true。
阿里均为 false，工具也为 false；网页禁用本地性格和提示词编辑，隐藏本地工具，保留
原设置用于切回。服务端拒绝改变不生效的人设和工具参数。Hensun 的摘要保存授权仍然
有效，但这些摘要不传入阿里候选，界面明确说明。

## 协议与计量

复用 `ConversationBackend`、设备 Opus 解码、PCM 编码、节奏控制、权限和播放协调器。
`Started` 不允许开始上传，等待 `Listening` 后发送 `SendSpeech`；输入结束仅一次
`StopSpeech`。`SpeechContent` 和 `RespondingContent` 为全量文本，不能拼接成增量。
事件绑定 task/dialog/round，缓冲有界，取消同步失效并关闭供应商连接。
供应商 `RespondingEnded`、编码完成、设备 `drained` 分离；确认设备 drain 后才发送
`LocalRespondingEnded`，没有物理 ACK 不能宣称扬声器播完。

共享用量函数显式接收 provider/operation，成功与输入取消均记录
`aliyun-dialog` / `managed_dialog`。费用保持 unknown，不能用宣传组合价计算未知应用
配置的实际账单。用量 API 新增 `managed_dialog_requests`，网页计入托管语音记录。

## 配置与回退

服务端设置 `ALIYUN_DIALOG_API_KEY`、`ALIYUN_DIALOG_URL`、`ALIYUN_DIALOG_WORKSPACE_ID`、
`ALIYUN_DIALOG_APP_ID`；不进入客户端或 Git。凭据/连接失败不跨路线自动切换。
`ALIYUN_DIALOG_ENABLED` 默认 false；生产还要求 `ALIYUN_DIALOG_VALIDATED`。
两者与目录启用状态均需检查。本地凭据已配置，两个开关仍为 false。

当前数据库列均为已有字符串，无结构迁移；`ensure_catalog` 幂等增加关闭的候选项。
发布顺序：先后端与网页，再验证环境开启目录，真机通过后才开放日常使用。
回退保留本版本兼容后端，关闭阿里开关和目录，保留角色及用量；受影响角色需手动切换。
旧后端不识别 `managed_app`，不能直接回退旧二进制而假定禁用记录不会影响管理接口。

协议依据：https://help.aliyun.com/zh/model-studio/multimodal-interaction-protocol

## 本地验证进展（2026-09-08）

- 使用本地配置凭据完成真实 `Start` → `Listening` 握手，随后主动关闭连接；
  该探测没有发送录音，不代表整机语音验收通过。
- 本地开启 `ALIYUN_DIALOG_ENABLED` 和候选目录，`ALIYUN_DIALOG_VALIDATED` 保持 false。
- 为当前无摄像头设备的账户新建“阿里多模态测试”角色并选中；原角色保留。
  原角色标识及切换记录保存在忽略的 `run/local-pilot/aliyun-test-role.json`。
- 5 项阿里协议/目录测试通过。测试须显式设置 `ALIYUN_DIALOG_ENABLED=false`，
  避免本地启用配置污染“默认关闭”测试前提。
- 当前串联路线网页提供 Cherry、Ethan 两个音色；阿里候选只提供应用默认音色。
- 真机收音、完整回复及连续多轮仍待用户实测，不做生产放行。
