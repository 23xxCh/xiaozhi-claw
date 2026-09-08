# 网页音色与情绪语音本地验收（2026-09-08）

- 快速对话、丰富表达扩展为 12 个 Qwen 音色；新增 10 个自有文本合成 WAV 试听，保留 Cherry/Ethan 原试听。网页支持名称/风格搜索，仍通过模型兼容目录筛选。
- 新增 expressive-chat，使用 qwen3-tts-instruct-flash-realtime-2026-01-22。仓库默认禁用；当前本地验证数据库已启用，显示“情绪语音（试用）”。旧角色、默认方案和设备当前角色均未改变，与启动前数据库备份比对一致。
- 同一回复的已解析情绪同时送入 TTS 和屏幕。普通 Qwen 不发送指令；未知情绪归为 neutral。实际接口只接受首次 session.update，故等待情绪再初始化，情绪变化时重新连接，同情绪复用连接。这会增加情绪切换时的延迟，尚未测量真机首响。

## 验证证据

- 普通模型 12/12 音色真实合成通过；Instruct 模型 12/12 通过。Cherry 连续 neutral → angry → neutral 合成通过，不代表已经主观评定语气质量。
- 本地记录：run/local-pilot/voice-expansion-probes.json（含修复前 Instruct 失败）、expressive-voice-probes.json（修复后）。不记录密钥或完整供应商事件。
- 相关测试 64 项通过（63 项组合回归，加 1 项首次配置回归）；Ruff 检查通过。网页生产构建与 TypeScript 检查通过，12 个试听 HTTP 200，新增 WAV 为 24kHz 单声道 16bit。
- 控制面 8000、网关 8001 健康检查 200；数据库目录返回 fast-chat/rich-chat/expressive-chat 各 12 音色，aliyun-dialog 仍为应用默认音色。
- 扩展运行 gateway_contract 时，49 项通过、1 项失败：已有 docs/openapi.json 与当前邮箱绑定、managed_app 等接口不一致；本次未更改这些接口或 OpenAPI 文件。

## 使用与回退

刷新角色页，选择“快速对话”试听新增声音；验证情绪时选择“情绪语音（试用）”，保存后再开启下一轮。阿里托管应用音色仍在阿里控制台配置，豆包和其他产品的音色不能跨模型混用。

真机待验：实际语气、表情对应、连续多轮、取消与断线、首响与尾音。未改变固件，没有商业放行。候选问题可切回快速对话；停用 expressive-chat 前应先将使用它的角色切回兼容方案，不覆盖数据库。启动前备份位于 run/backups/20260908-214827/，仅用于受控恢复，不能直接覆盖后续用户数据。

## 官方依据

- https://help.aliyun.com/zh/model-studio/qwen-tts-voice-list
- https://help.aliyun.com/zh/model-studio/qwen-tts-realtime-client-events
- https://help.aliyun.com/zh/model-studio/qwen3-tts-instruct-flash-realtime
