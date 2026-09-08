# 火山 TTS 2.0 本地候选验收（2026-09-08）

新增 volc-tts-chat：阿里 ASR＋DeepSeek＋火山 TTS 2.0。复用句子切分、审查、PCM→Opus、播放确认和角色配置版本机制。使用官方 V3 SSE 接口逐句输入、流式输出，不宣称已实现双向文本流或火山情绪指令。

服务器配置：VOLC_TTS_API_KEY（独立配置，保存在本地忽略的 .env）；VOLC_TTS_URL 默认 https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse。凭据未写入仓库、网页、固件或报告。供应商资源 seed-tts-2.0 与端到端语音分开，拒绝混用音色；火山 TTS 失败不会自动切到阿里 TTS。

首批 VV 2.0、小何 2.0、M191 2.0 男声，3/3 完整合成通过，音频均为 24kHz 单声道 S16LE，网页提供本地 WAV 试听。一个未验证的“小天”候选 ID 返回 55000000，未加入目录，不能据此断言官方小天不可用。此次未接入全部火山音色。

真实连续三轮 VV 测试均合成并通过现有 FFmpeg 编码器，每轮输出 60 个 Opus 包。此证据仅覆盖服务端音频链路，不等于真机已经实听。记录在 run/local-pilot/volc-tts-probes.json（不入库/仓库），试听文件为本项目自拟文本生成。

相关自动测试 67 项通过：SSE 结束、截流、空音频、服务错误、HTTP 错误、取消后拒绝新文本、模型音色兼容、现有语速/回退回归，以及火山失败不自动换阿里。Ruff、网页生产构建及 TypeScript 通过；3 个试听 HTTP 200，控制面/网关健康检查 200。

仓库预设默认禁用，本地验证库已开放“火山音色（试用）”。数据库目录返回 3 个兼容音色；既有设备活动角色、角色模型/音色/配置版本与启动前备份比对不变。新方案不是默认。无需刷固件。

TTS 成本暂按官网公示 5 元/万字符进入现有估算字段；实际折扣、套餐、供应商账单需单独核对，页面用量不是供应商结算凭证。

使用：刷新角色页，选择火山音色（试用），选择并试听声音，保存，下一轮生效。真机还需验证收尾、取消、断网、多轮以及实际首响。回退可将角色切回快速对话及其兼容声音，随后禁用候选；不要用旧数据库覆盖新用户数据。启动前备份：run/backups/20260908-221115/hensun-lan.db。

官方依据：
- https://www.volcengine.com/docs/6561/1598757
- https://github.com/bytedance/agentkit-samples/blob/main/skills/byted-text-to-speech/scripts/text_to_speech.py
- https://www.volcengine.com/product/doubao
