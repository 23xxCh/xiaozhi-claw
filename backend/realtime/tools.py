"""Bounded server-side tools exposed to the realtime LLM gateway.

The registry deliberately keeps tool credentials and provider URLs on the
server.  Agents opt in by setting a boolean entry in ``tools_json``; unknown
names are never exposed to the model.
"""

from __future__ import annotations

import ast
import asyncio
import json
import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from backend.app.config import Settings


class ToolError(RuntimeError):
    """A user-safe tool failure; provider details are intentionally omitted."""


ToolHandler = Callable[[dict[str, object]], Awaitable[str]]


class SearchProvider(Protocol):
    async def search(self, query: str) -> str: ...


class MockSearchProvider:
    """Deterministic adapter used by tests and offline development only."""

    async def search(self, query: str) -> str:
        return f"模拟搜索：未连接联网服务。查询词为“{query[:160]}”。"


class DashScopeWebSearchProvider:
    """Minimal Streamable HTTP client for Alibaba Model Studio WebSearch MCP."""

    def __init__(self, endpoint: str, api_key: str, timeout_seconds: float) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_seconds = min(max(timeout_seconds, 3.0), 15.0)

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, object]:
        if not response.content:
            return {}
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            payload = json.loads(data)
            if isinstance(payload, dict):
                return payload
        return {}

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        *,
        session_id: str = "",
    ) -> tuple[dict[str, object], str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-03-26",
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        response = await client.post(self.endpoint, headers=headers, json=payload)
        response.raise_for_status()
        return self._decode_response(response), response.headers.get("Mcp-Session-Id", session_id)

    async def search(self, query: str) -> str:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                initialized, session_id = await self._post(
                    client,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "hensun-realtime-gateway", "version": "0.1"},
                        },
                    },
                )
                if "error" in initialized:
                    raise ToolError("联网搜索暂时不可用")
                await self._post(
                    client,
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    session_id=session_id,
                )
                listed, session_id = await self._post(
                    client,
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    session_id=session_id,
                )
                result = listed.get("result")
                tools = result.get("tools") if isinstance(result, dict) else None
                names = [
                    str(item.get("name"))
                    for item in tools or []
                    if isinstance(item, dict) and isinstance(item.get("name"), str)
                ]
                tool_name = next(
                    (name for name in names if name == "bailian_web_search"),
                    next((name for name in names if "web_search" in name.lower()), ""),
                )
                if not tool_name:
                    raise ToolError("联网搜索暂时不可用")
                called, _ = await self._post(
                    client,
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": {"query": query}},
                    },
                    session_id=session_id,
                )
                result = called.get("result")
                if not isinstance(result, dict) or result.get("isError"):
                    raise ToolError("联网搜索暂时不可用")
                content = result.get("content")
                text = "\n".join(
                    str(item.get("text"))
                    for item in content or []
                    if isinstance(item, dict) and item.get("type") == "text"
                ).strip()
                if not text:
                    raise ToolError("没有找到可用的搜索结果")
                return text[:6000]
        except ToolError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise ToolError("联网搜索暂时不可用") from exc


class DashScopeQwenSearchProvider:
    """Qwen Chat Completions search adapter used when MCP is not activated."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
    ) -> None:
        base_url = endpoint.rstrip("/")
        self.endpoint = (
            base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        )
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = min(max(timeout_seconds, 3.0), 15.0)
        self._cache: dict[str, str] = {}

    async def search(self, query: str) -> str:
        cached = self._cache.get(query)
        if cached is not None:
            return cached
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": (
                                f"当前 UTC 时间：{datetime.now(UTC).isoformat()}。"
                                "查询真实最新信息，最多150字，保留信息日期和来源；"
                                "查不到明确说明，禁止推测。"
                            )},
                            {"role": "user", "content": query},
                        ],
                        "max_tokens": 400,
                        "enable_thinking": False,
                        "enable_search": True,
                        "search_options": {
                            "forced_search": True,
                            "enable_source": True,
                        },
                    },
                )
                response.raise_for_status()
                payload = response.json()
                choices = payload.get("choices") if isinstance(payload, dict) else None
                message = (
                    choices[0].get("message")
                    if isinstance(choices, list) and choices
                    else None
                )
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, str) or not content.strip():
                    raise ToolError("没有找到可用的搜索结果")
                result = content.strip()[:6000]
                self._cache[query] = result
                return result
        except ToolError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise ToolError("联网搜索暂时不可用") from exc


def create_search_provider(settings: Settings) -> SearchProvider | None:
    if settings.provider_mode == "mock":
        return MockSearchProvider()
    api_key = settings.web_search_mcp_api_key or settings.asr_api_key
    if settings.web_search_qwen_enabled and api_key:
        return DashScopeQwenSearchProvider(
            settings.web_search_qwen_url,
            api_key,
            settings.web_search_qwen_model,
            settings.provider_timeout_seconds,
        )
    if not settings.web_search_mcp_enabled or not api_key:
        return None
    return DashScopeWebSearchProvider(
        settings.web_search_mcp_url,
        api_key,
        settings.provider_timeout_seconds,
    )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, object]
    risk: str = "low"
    handler: ToolHandler | None = None
    available: bool = True

    def openai_schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _calculate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _calculate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _calculate(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left = _calculate(node.left)
        right = _calculate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ToolError("计算范围过大")
        value = _OPERATORS[type(node.op)](left, right)
        if abs(value) > 1e12:
            raise ToolError("计算结果超出范围")
        return value
    raise ToolError("只支持基本四则运算")


async def _current_time(arguments: dict[str, object]) -> str:
    del arguments
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


async def _calculator(arguments: dict[str, object]) -> str:
    expression = arguments.get("expression")
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 120:
        raise ToolError("请提供简短的计算式")
    try:
        result = _calculate(ast.parse(expression, mode="eval"))
    except (SyntaxError, ZeroDivisionError, OverflowError, ToolError) as exc:
        if isinstance(exc, ToolError):
            raise
        raise ToolError("计算式无法处理") from exc
    return str(int(result)) if result.is_integer() else f"{result:.8g}"


async def _unconfigured_remote_tool(arguments: dict[str, object]) -> str:
    del arguments
    raise ToolError("该联网工具尚未配置")


def _search_handler(provider: SearchProvider) -> ToolHandler:
    async def handler(arguments: dict[str, object]) -> str:
        query = arguments.get("query")
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 160:
            raise ToolError("请提供有效的搜索词")
        return await provider.search(query.strip())

    return handler


def _weather_handler(provider: SearchProvider) -> ToolHandler:
    async def handler(arguments: dict[str, object]) -> str:
        city = arguments.get("city")
        if not isinstance(city, str) or not 1 <= len(city.strip()) <= 40:
            raise ToolError("请提供有效的城市名称")
        return await provider.search(f"{city.strip()} 当前天气、温度和降水情况")

    return handler


class ToolRegistry:
    """Per-device registry with a small, explicit allowlist."""

    def __init__(self, *, search_provider: SearchProvider | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.register(
            ToolDefinition(
                "current_time",
                "获取当前本地时间。",
                {"type": "object", "properties": {}, "additionalProperties": False},
                handler=_current_time,
            )
        )
        self.register(
            ToolDefinition(
                "calculator",
                "计算简单的四则运算。",
                {
                    "type": "object",
                    "properties": {"expression": {"type": "string", "maxLength": 120}},
                    "required": ["expression"],
                    "additionalProperties": False,
                },
                handler=_calculator,
            )
        )
        for name, description, properties in (
            (
                "weather",
                "查询指定城市的天气。用户只说省份或地区而未指定城市时，"
                "先追问具体城市，不要擅自选择省会。查询失败不代表该地区没有天气数据。",
                {"city": {"type": "string", "maxLength": 40,
                          "description": "用户指定的城市或区县，不是省份"}},
            ),
            (
                "web_search",
                "搜索最新公开网页信息；回答时说明信息可能随时间变化。",
                {"query": {"type": "string", "minLength": 2, "maxLength": 160}},
            ),
        ):
            handler = _unconfigured_remote_tool
            if search_provider is not None:
                handler = (
                    _search_handler(search_provider)
                    if name == "web_search"
                    else _weather_handler(search_provider)
                )
            self.register(
                ToolDefinition(
                    name,
                    description,
                    {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    },
                    handler=handler,
                    available=search_provider is not None,
                )
            )

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            return
        self._tools[definition.name] = definition

    def register_remote(
        self,
        name: str,
        description: str,
        parameters: dict[str, object],
        handler: ToolHandler,
        *,
        risk: str = "device",
    ) -> None:
        if not name.startswith("self."):
            return
        self.register(ToolDefinition(name, description[:500], parameters, risk, handler))

    def definitions(self, enabled: dict[str, bool]) -> list[dict[str, object]]:
        return [
            definition.openai_schema()
            for name, definition in self._tools.items()
            if enabled.get(name, False)
            and definition.handler is not None
            and definition.available
        ]

    async def execute(self, name: str, arguments: dict[str, object]) -> str:
        definition = self._tools.get(name)
        if definition is None or definition.handler is None:
            raise ToolError("工具未启用")
        if not definition.available:
            raise ToolError("该联网工具尚未配置")
        try:
            return await asyncio.wait_for(definition.handler(arguments), timeout=8.0)
        except TimeoutError as exc:
            raise ToolError("工具响应超时") from exc
