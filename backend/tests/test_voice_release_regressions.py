import asyncio
from dataclasses import replace

import pytest
from starlette.websockets import WebSocketDisconnect

from backend.realtime.commands import DeviceCommandDispatcher
from backend.realtime.session import _receive_device_message

from .conftest import provision_owned_device
from .test_websocket import _device_headers, _receive_mock_turn


def test_simultaneous_session_exit_keeps_already_received_ping():
    async def run():
        stopped = asyncio.Event()
        stopped.set()

        class Socket:
            async def receive(self):
                return {"type": "websocket.receive", "text": '{"type":"ping"}'}

        message = await _receive_device_message(
            Socket(), timeout_seconds=1, stop_event=stopped,
        )
        assert message is not None and message["text"] == '{"type":"ping"}'

    asyncio.run(run())


@pytest.mark.parametrize("chunk_size", [1, 45])
def test_transport_chunks_do_not_truncate_a_valid_reply(client, admin_headers, chunk_size):
    reply = (
        "这是一段用于验证语音不会因为传输分块而丢失结尾的完整回复"
        "请务必把最后几个字也全部朗读出来。"
    )
    assert len(reply) < 60

    class Llm:
        async def aclose(self):
            pass

        async def reply_stream(self, request, *, tool_executor=None):
            for offset in range(0, len(reply), chunk_size):
                yield reply[offset:offset + chunk_size]

    client.app.state.realtime_providers = replace(client.app.state.realtime_providers, llm=Llm())
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as ws:
        ws.send_json({"type": "listen", "state": "start"})
        ws.send_bytes("你好".encode())
        ws.send_json({"type": "listen", "state": "stop"})
        _, audio = _receive_mock_turn(ws)
        assert audio.decode() == reply


@pytest.mark.parametrize("implicit", [False, True])
def test_unbound_socket_cannot_start_another_turn(client, admin_headers, implicit):
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as ws:
        response = client.post(
            f"/v1/devices/{owned['device_id']}/unbind",
            headers={"Authorization": f"Bearer {owned['user_token']}"},
        )
        assert response.status_code == 200
        if not implicit:
            ws.send_json({"type": "listen", "state": "start"})
        ws.send_bytes("仍然在说话".encode())
        ws.send_json({"type": "listen", "state": "stop"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4403


def test_unbind_outbox_retires_idle_socket_without_another_device_message(
    client, admin_headers,
):
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as ws:
        response = client.post(
            f"/v1/devices/{owned['device_id']}/unbind",
            headers={"Authorization": f"Bearer {owned['user_token']}"},
        )
        assert response.status_code == 200
        dispatcher = DeviceCommandDispatcher(
            client.app.state.session_factory, client.app.state.device_connections, 0.5,
        )
        client.portal.call(dispatcher._dispatch_batch)
        assert not client.portal.call(
            client.app.state.device_connections.is_connected, owned["serial"],
        )
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4403


def test_unbind_cancels_running_provider_work(client, admin_headers):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    class WaitingLlm:
        async def aclose(self):
            pass

        async def reply_stream(self, request, *, tool_executor=None):
            entered.set()
            try:
                await asyncio.Event().wait()
                yield "must not be spoken"
            finally:
                cancelled.set()

    client.app.state.realtime_providers = replace(
        client.app.state.realtime_providers, llm=WaitingLlm(),
    )
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as ws:
        ws.send_json({"type": "listen", "state": "start"})
        ws.send_bytes("你好".encode())
        ws.send_json({"type": "listen", "state": "stop"})
        client.portal.call(asyncio.wait_for, entered.wait(), 2)
        assert client.post(
            f"/v1/devices/{owned['device_id']}/unbind",
            headers={"Authorization": f"Bearer {owned['user_token']}"},
        ).status_code == 200
        dispatcher = DeviceCommandDispatcher(
            client.app.state.session_factory, client.app.state.device_connections, 0.5,
        )
        client.portal.call(dispatcher._dispatch_batch)
        client.portal.call(asyncio.wait_for, cancelled.wait(), 2)
