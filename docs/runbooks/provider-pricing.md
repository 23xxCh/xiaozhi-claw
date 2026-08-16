# Provider 目录价与成本口径

更新时间：2026-08-13

控制台中的成本是按公开目录价计算的运营估算，不等同于服务商最终账单。免费额度、缓存命中、活动折扣、税费和服务商后续调价可能造成差异；研发人员可以通过模型预设管理接口调整费率。

## 当前默认费率

| 模型 | 计费单位 | 默认目录价 |
|---|---|---:|
| `qwen3-asr-flash-realtime` | 华北2（北京），输入音频 | 0.00033 元/秒，即 0.0198 元/分钟 |
| `deepseek-v4-flash` | 缓存未命中输入 / 输出 | 1 元 / 2 元每百万 Token |
| `deepseek-v4-pro` | 缓存未命中输入 / 输出 | 3 元 / 6 元每百万 Token |
| `qwen3-tts-flash-realtime` | 华北2（北京），输入文本 | 1 元/万字符 |
| `qwen3-asr-flash` | 华北2（北京），输入音频 | 0.00022 元/秒，即 0.0132 元/分钟 |
| `qwen3.7-flash` | 32K 以内输入 / 输出 | 0.2 元 / 0.8 元每百万 Token |
| `qwen3-tts-flash` | 华北2（北京），输入文本 | 0.8 元/万字符 |

来源：

- [阿里云百炼模型调用价格](https://help.aliyun.com/zh/model-studio/model-pricing)
- [DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

## 数据口径

- ASR `input_units` 为输入音频毫秒数。
- TTS `input_units` 为送入语音合成的字符数。
- 当前 DeepSeek 流式接口未持久化逐句正文，LLM Token 使用字符数进行保守估算；接入服务商最终 usage 字段后再替换为账单级 Token。
- `fallback-batch` 表示实时链路失败后完成了批量降级，不计为整轮失败，但会单独进入降级率。
- 启动时只为四项费率全部为零的内置预设填入目录价，避免覆盖管理员已经调整的费率。
