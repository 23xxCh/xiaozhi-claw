"""Exercise the gateway's live route gate and device-tool permission mapping."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.app.models import Agent, Device
from backend.realtime import session as realtime_session
from backend.realtime.mcp import DeviceMcpClient

from .conftest import provision_owned_device
from .test_s2s_gateway import FakeBackend, FakeDecoder, configure_s2s


def _device_headers(owned):
    return {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
    }


def test_live_production_gate_blocks_a_previously_enabled_catalog(
    client, admin_headers, monkeypatch
):
    owned = provision_owned_device(client, admin_headers)
    configure_s2s(client, owned)
    # Construction also validates this combination. Simulate runtime revocation
    # so a previously enabled catalog cannot be used after validation is withdrawn.
    settings = client.app.state.settings
    settings.doubao_realtime_enabled = True
    settings.doubao_api_key = "test-key-not-a-credential"
    settings.app_env = "production"
    settings.doubao_realtime_validated = False
    open_backend = AsyncMock(side_effect=AssertionError("closed route opened its supplier"))
    monkeypatch.setattr(realtime_session.DoubaoRealtimeBackend, "open", open_backend)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as socket:
        socket.send_json({"type": "hello", "version": 1})
        assert socket.receive_json()["type"] == "hello"
        socket.send_json({"type": "listen", "state": "start"})
        assert socket.receive_json()["code"] == "s2s-not-enabled"
    open_backend.assert_not_awaited()


@pytest.mark.parametrize(
    "alias,setting_name,arguments",
    [
        ("device_set_volume", "self.audio_speaker.set_volume", {"volume": 45}),
        ("device_set_brightness", "self.screen.set_brightness", {"brightness": 55}),
    ],
)
def test_s2s_device_tool_alias_uses_canonical_permission_and_honors_revocation(
    client, admin_headers, monkeypatch, alias, setting_name, arguments
):
    owned = provision_owned_device(client, admin_headers)
    configure_s2s(client, owned)
    settings = client.app.state.settings
    settings.doubao_realtime_enabled = True
    settings.doubao_api_key = "test-key-not-a-credential"
    client.app.state.realtime_providers.mock = False
    headers = {"Authorization": f"Bearer {owned['user_token']}"}

    async def agent_id():
        async with client.app.state.session_factory() as db:
            device = await db.get(Device, owned["device_id"])
            return device.active_agent_id

    active_agent_id = client.portal.call(agent_id)
    path = f"/v1/agents/{active_agent_id}"
    enabled = client.patch(path, headers=headers, json={"tools": {setting_name: True}})
    assert enabled.status_code == 200, enabled.text

    initialized = asyncio.Event()
    calls = []

    class LocalDeviceMcp(DeviceMcpClient):
        async def initialize(self):
            self._tools = {setting_name: {"inputSchema": {"type": "object"}}}
            initialized.set()

        async def _request(self, method, params=None):
            calls.append((method, params))
            return {"result": {"content": [{"type": "text", "text": "applied"}]}}

    backend = FakeBackend()
    opened = asyncio.Event()
    captured = {}

    async def open_backend(*_args, **kwargs):
        captured.update(kwargs)
        opened.set()
        return backend

    monkeypatch.setattr(realtime_session, "DeviceMcpClient", LocalDeviceMcp)
    monkeypatch.setattr(realtime_session.DoubaoRealtimeBackend, "open", open_backend)
    monkeypatch.setattr(realtime_session, "StreamingOpusToPcm", FakeDecoder)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as socket:
        socket.send_json({"type": "hello", "version": 1})
        assert socket.receive_json()["type"] == "hello"
        client.portal.call(asyncio.wait_for, initialized.wait(), 2)
        socket.send_json({"type": "listen", "state": "start"})
        client.portal.call(asyncio.wait_for, opened.wait(), 2)
        socket.send_bytes(b"test-device-opus")
        socket.send_json({"type": "listen", "state": "stop"})
        client.portal.call(lambda: backend.emit("transcript_final", text="请调整设备设置"))
        assert socket.receive_json()["type"] == "stt"
        assert socket.receive_json()["emotion"] == "thinking"
        execute_tool = captured["tool_executor"]
        assert client.portal.call(execute_tool, alias, arguments) == "applied"
        assert calls == [("tools/call", {"name": setting_name, "arguments": arguments})]

        disabled = client.patch(path, headers=headers, json={"tools": {setting_name: False}})
        assert disabled.status_code == 200, disabled.text
        with pytest.raises(RuntimeError, match="tool is not enabled"):
            client.portal.call(execute_tool, alias, arguments)
        assert len(calls) == 1

        async def stored_permissions():
            async with client.app.state.session_factory() as db:
                agent = await db.get(Agent, active_agent_id)
                return agent.tools_json

        assert alias not in client.portal.call(stored_permissions)
