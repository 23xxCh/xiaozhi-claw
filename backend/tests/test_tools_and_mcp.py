import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.ai.context import ContextBuilder, LlmRequest
from backend.realtime import providers as realtime_providers
from backend.realtime import tools as realtime_tools
from backend.realtime.mcp import DeviceMcpClient, DeviceMcpError
from backend.realtime.providers import DeepSeekStreamingLlmProvider
from backend.realtime.tools import (
    DashScopeQwenSearchProvider,
    DashScopeWebSearchProvider,
    MockSearchProvider,
    ToolError,
    ToolRegistry,
)


@pytest.mark.asyncio
async def test_tool_registry_is_opt_in_and_calculator_is_bounded() -> None:
    registry = ToolRegistry()
    assert registry.definitions({}) == []
    tools = registry.definitions({"calculator": True})
    assert [item["function"]["name"] for item in tools] == ["calculator"]
    assert registry.definitions({"web_search": True, "weather": True}) == []
    assert await registry.execute("calculator", {"expression": "2 + 3 * 4"}) == "14"
    with pytest.raises(ToolError):
        await registry.execute("calculator", {"expression": "__import__('os').getcwd()"})
    with pytest.raises(ToolError, match="尚未配置"):
        await registry.execute("web_search", {"query": "Hensun"})
    offline = ToolRegistry(search_provider=MockSearchProvider())
    assert "模拟搜索" in await offline.execute("web_search", {"query": "Hensun"})


class _Connections:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_json_for_lease(self, lease, payload: dict[str, object]) -> bool:
        del lease
        self.sent.append(payload)
        return True


@pytest.mark.asyncio
async def test_device_mcp_initializes_lists_and_calls_tools() -> None:
    connections = _Connections()
    lease = SimpleNamespace(serial_number="SOAK-1")
    client = DeviceMcpClient(lease, connections)  # type: ignore[arg-type]
    task = asyncio.create_task(client.initialize())
    await asyncio.sleep(0)
    first = connections.sent[0]["payload"]
    assert first["method"] == "initialize"  # type: ignore[index]
    assert client.handle_message(
        {"type": "mcp", "payload": {"jsonrpc": "2.0", "id": 1, "result": {}}}
    )
    await asyncio.sleep(0)
    second = connections.sent[1]["payload"]
    assert second["method"] == "tools/list"  # type: ignore[index]
    assert client.handle_message(
        {
            "type": "mcp",
            "payload": {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "self.audio_speaker.set_volume",
                            "description": "Set volume",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                },
            },
        }
    )
    await task
    assert client.openai_tools({"self.audio_speaker.set_volume": True})
    assert client.openai_tools({"self.audio_speaker.set_volume": True})[0]["function"][
        "name"
    ] == "device_set_volume"
    with pytest.raises(DeviceMcpError, match="not available"):
        await client.call("self.device.reboot", {})
    call_task = asyncio.create_task(
        client.call("device_set_volume", {"volume": 40})
    )
    await asyncio.sleep(0)
    call_payload = connections.sent[2]["payload"]
    assert call_payload["method"] == "tools/call"  # type: ignore[index]
    assert client.handle_message(
        {
            "type": "mcp",
            "payload": {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {"content": [{"type": "text", "text": "true"}]},
            },
        }
    )
    assert await call_task == "true"
    with pytest.raises(DeviceMcpError, match="invalid device tool value"):
        await client.call("device_set_volume", {"volume": 101})


@pytest.mark.asyncio
async def test_dashscope_web_search_uses_streamable_http(monkeypatch) -> None:
    methods: list[str] = []
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload["method"]
        methods.append(method)
        headers = {"Mcp-Session-Id": "session-1"}
        if method == "initialize":
            return httpx.Response(
                200,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "result": {}},
            )
        if method == "notifications/initialized":
            return httpx.Response(202, headers=headers)
        if method == "tools/list":
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": [{"name": "bailian_web_search"}]},
                },
            )
        assert payload["params"]["arguments"] == {"query": "Hensun"}
        return httpx.Response(
            200,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "result": {"content": [{"type": "text", "text": "搜索结果"}]},
            },
        )

    def client_factory(*args, **kwargs) -> httpx.AsyncClient:
        del args, kwargs
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(realtime_tools.httpx, "AsyncClient", client_factory)
    provider = DashScopeWebSearchProvider(
        "https://dashscope.example/mcp", "secret", 8
    )
    assert await provider.search("Hensun") == "搜索结果"
    assert methods == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]


@pytest.mark.asyncio
async def test_dashscope_qwen_search_forces_real_web_search(monkeypatch) -> None:
    requests: list[dict[str, object]] = []
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "带来源的联网搜索结果"}}
                ]
            },
        )

    def client_factory(*args, **kwargs) -> httpx.AsyncClient:
        del args, kwargs
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(realtime_tools.httpx, "AsyncClient", client_factory)
    provider = DashScopeQwenSearchProvider(
        "https://dashscope.example/compatible-mode/v1", "secret", "qwen-plus", 8
    )

    assert await provider.search("Hensun") == "带来源的联网搜索结果"
    assert await provider.search("Hensun") == "带来源的联网搜索结果"
    assert len(requests) == 1
    assert requests[0]["model"] == "qwen-plus"
    assert requests[0]["enable_search"] is True
    assert requests[0]["search_options"] == {
        "forced_search": True,
        "enable_source": True,
    }


@pytest.mark.asyncio
async def test_deepseek_tool_call_round_trip(monkeypatch) -> None:
    requests: list[dict[str, object]] = []
    original_client = httpx.AsyncClient
    responses = [
        (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
            '"function":{"name":"calculator","arguments":"{\\"expression\\":\\"2+2\\"}"}}]}}]}\n\n'
            'data: [DONE]\n\n'
        ),
        'data: {"choices":[{"delta":{"content":"结果是4"}}]}\n\ndata: [DONE]\n\n',
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, text=responses.pop(0))

    def client_factory(*args, **kwargs) -> httpx.AsyncClient:
        del args, kwargs
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(realtime_providers.httpx, "AsyncClient", client_factory)
    provider = DeepSeekStreamingLlmProvider(
        realtime_providers.Settings(
            provider_mode="custom",
            llm_url="https://llm.example/v1",
            llm_api_key="secret",
            llm_model="deepseek-v4-flash",
        )
    )

    async def execute(name: str, arguments: dict[str, object]) -> str:
        assert name == "calculator"
        assert arguments == {"expression": "2+2"}
        return "4"

    chunks = [
        chunk
        async for chunk in provider.reply_stream(
            LlmRequest(
                context=ContextBuilder().build(
                    system_prompt="你是助手",
                    current_question="算一下",
                    history=[],
                    memories=[],
                    summaries=[],
                    tools=[{"type": "function", "function": {"name": "calculator"}}],
                ),
                model="deepseek-v4-flash",
                temperature=0.3,
                tools=[{"type": "function", "function": {"name": "calculator"}}],
            ),
            tool_executor=execute,
        )
    ]
    assert chunks == ["结果是4"]
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert requests[1]["tool_choice"] == "none"
