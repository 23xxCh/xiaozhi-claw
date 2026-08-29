"""Small JSON-RPC client for the device-side MCP server."""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

from backend.app.device_connections import ConnectionLease, DeviceConnectionManager


class DeviceMcpError(RuntimeError):
    pass


class DeviceMcpClient:
    SAFE_TOOL_ALIASES = {
        "device_set_volume": "self.audio_speaker.set_volume",
        "device_set_brightness": "self.screen.set_brightness",
    }

    def __init__(
        self, lease: ConnectionLease, connections: DeviceConnectionManager
    ) -> None:
        self.lease = lease
        self.serial = lease.serial_number
        self.connections = connections
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._tools: dict[str, dict[str, Any]] = {}

    async def _request(
        self, method: str, params: dict[str, object] | None = None
    ) -> dict[str, Any]:
        request_id = next(self._ids)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        sent = await self.connections.send_json_for_lease(
            self.lease,
            {
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                },
            },
        )
        if not sent:
            self._pending.pop(request_id, None)
            raise DeviceMcpError("device is offline")
        try:
            return await asyncio.wait_for(future, timeout=8.0)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise DeviceMcpError("device MCP response timed out") from exc

    async def initialize(self) -> None:
        await self._request("initialize", {"capabilities": {}})
        response = await self._request("tools/list", {"cursor": "", "withUserTools": False})
        result = response.get("result")
        if not isinstance(result, dict):
            return
        tools = result.get("tools")
        if isinstance(tools, list):
            self._tools = {
                str(item.get("name")): item
                for item in tools
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }

    def handle_message(self, message: dict[str, object]) -> bool:
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return False
        request_id = payload.get("id")
        if not isinstance(request_id, int):
            return False
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        if "error" in payload:
            error = payload.get("error")
            message_text = error.get("message") if isinstance(error, dict) else "device MCP error"
            future.set_exception(DeviceMcpError(str(message_text)[:160]))
        else:
            future.set_result(payload)
        return True

    def openai_tools(self, enabled: dict[str, bool]) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for alias, name in self.SAFE_TOOL_ALIASES.items():
            item = self._tools.get(name)
            if item is None or not enabled.get(name, False):
                continue
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": alias,
                        "description": str(item.get("description") or "设备工具"),
                        "parameters": item.get(
                            "inputSchema", {"type": "object", "properties": {}}
                        ),
                    },
                }
            )
        return result

    def can_call(self, name: str) -> bool:
        return name in self.SAFE_TOOL_ALIASES

    @staticmethod
    def _validate_arguments(tool_name: str, arguments: dict[str, object]) -> None:
        parameter = "volume" if tool_name.endswith("set_volume") else "brightness"
        if set(arguments) != {parameter}:
            raise DeviceMcpError("invalid device tool arguments")
        value = arguments.get(parameter)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
            raise DeviceMcpError("invalid device tool value")

    async def call(self, name: str, arguments: dict[str, object]) -> str:
        tool_name = self.SAFE_TOOL_ALIASES.get(name)
        if tool_name is None or tool_name not in self._tools:
            raise DeviceMcpError("device tool is not available")
        self._validate_arguments(tool_name, arguments)
        response = await self._request(
            "tools/call", {"name": tool_name, "arguments": arguments}
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise DeviceMcpError("invalid device MCP result")
        if result.get("isError"):
            raise DeviceMcpError("device tool failed")
        content = result.get("content")
        if isinstance(content, list):
            texts = [str(item.get("text")) for item in content if isinstance(item, dict)]
            return "\n".join(texts)[:4000]
        return str(result)[:4000]

    async def close(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(DeviceMcpError("device connection closed"))
        self._pending.clear()
