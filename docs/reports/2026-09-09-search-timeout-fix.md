# Weather and news search timeout

The active device role uses fast-chat with weather and web_search enabled.
Two controlled text requests through DeepSeekStreamingLlmProvider and the real
ToolRegistry reproduced ToolError at the registry's eight-second deadline.
The LLM then generated an unavailable-service answer. Enabling the flags alone
was insufficient evidence of working voice search.

The Qwen search request now disables thinking, bounds output to 400 tokens,
requests a short factual summary with date/source, and supplies current UTC time.
The existing eight-second cancellation limit remains. Tool telemetry records
names, turn IDs, duration and exception type, without arguments or result text.

After the change, the same real LLM-to-tool-to-answer probes completed:

| Query | Tool time | Complete text response |
| --- | ---: | ---: |
| Beijing weather today | 3.88 s | 5.66 s |
| Today's technology news | 5.56 s | 8.72 s |

These two samples demonstrate invocation and result consumption, not a latency
percentile or independent fact verification. The news answer explicitly lacked
same-day results and summarized recent items; freshness remains a quality limit.
Physical microphone, speaker, watchdog and multi-turn acceptance remain pending.

Validation: backend/tests/test_tools_and_mcp.py, five passed; git diff --check.
Local gateway restarted with this code; API and gateway live/ready return 200.
No firmware change or flash.

Protocol reference: https://help.aliyun.com/zh/model-studio/web-search
