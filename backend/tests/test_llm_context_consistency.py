import json
from datetime import UTC, datetime

import httpx
import pytest

from backend.ai.context import ConfirmedMemory, ContextBuilder, ContextSummary, LlmRequest
from backend.app.config import Settings
from backend.app.providers import OpenAICompatibleLlmProvider
from backend.realtime import providers as realtime_providers
from backend.realtime.providers import DeepSeekStreamingLlmProvider


@pytest.mark.asyncio
async def test_primary_and_batch_fallback_receive_identical_messages(monkeypatch) -> None:
    captured_primary: dict[str, object] = {}
    captured_fallback: dict[str, object] = {}
    settings = Settings(
        provider_mode="custom",
        llm_url="https://llm.example/v1",
        llm_api_key="secret",
        llm_model="deepseek-v4-flash",
    )
    moment = datetime(2026, 8, 29, tzinfo=UTC)
    context = ContextBuilder().build(
        system_prompt="你是小灿。",
        current_question="继续刚才的话题",
        history=[
            {"role": "user", "content": "我周五要交报告"},
            {"role": "assistant", "content": "记住了。"},
        ],
        memories=[
            ConfirmedMemory("memory-1", "preferred_name", "请叫我阿辰", "name", moment)
        ],
        summaries=[ContextSummary("summary-1", "最近讨论过工作计划", moment)],
        tools=None,
    )
    request = LlmRequest(context=context, model="deepseek-v4-flash", temperature=0.3)

    original_client = httpx.AsyncClient

    def primary_handler(http_request: httpx.Request) -> httpx.Response:
        captured_primary.update(json.loads(http_request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n',
        )

    def primary_client_factory(*args, **kwargs) -> httpx.AsyncClient:
        del args, kwargs
        return original_client(transport=httpx.MockTransport(primary_handler))

    monkeypatch.setattr(realtime_providers.httpx, "AsyncClient", primary_client_factory)
    primary = DeepSeekStreamingLlmProvider(settings)
    assert [chunk async for chunk in primary.reply_stream(request)] == ["好"]

    def fallback_handler(http_request: httpx.Request) -> httpx.Response:
        captured_fallback.update(json.loads(http_request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "好"}}]})

    async with original_client(transport=httpx.MockTransport(fallback_handler)) as client:
        fallback = OpenAICompatibleLlmProvider(settings, client=client)
        assert await fallback.reply(request.with_model("qwen3.7-flash")) == "好"

    assert captured_primary["messages"] == captured_fallback["messages"]
