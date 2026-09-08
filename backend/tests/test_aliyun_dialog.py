import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import Agent, Device, ModelPreset, ProviderUsage
from backend.realtime import aliyun_dialog, s2s
from backend.realtime.aliyun_dialog import AliyunDialogBackend, AliyunDialogConfig
from backend.realtime.providers import RealtimeProviderError

from .conftest import provision_owned_device
from .test_s2s_gateway import FakeDecoder, FakeEncoder, receive_one_turn
from .test_voice_routes import _staff_headers, _user_headers


class AliSocket:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.sent = []
        self.task_id = ""
        self.closed = False

    def feed(self, event, **kwargs):
        self.queue.put_nowait(json.dumps({
            "header": {"task_id": self.task_id, "event": "result-generated"},
            "payload": {"output": {"event": event, "dialog_id": "ali-session", **kwargs}},
        }))

    async def send(self, raw):
        self.sent.append(raw)
        if isinstance(raw, bytes):
            return
        data = json.loads(raw)
        self.task_id = data["header"]["task_id"]
        directive = data["payload"]["input"]["directive"]
        if directive == "Start":
            assert len(data["payload"]["parameters"]["client_info"]["user_id"]) <= 32
            self.feed("Started")
            self.feed("DialogStateChanged", state="Listening")
        elif directive == "StopSpeech":
            self.feed("SpeechContent", text="你好", finished=True, round_id="ali-round")
            self.feed("SpeechContent", text="你好", finished=True, round_id="ali-round")
            self.feed("SpeechEnded", round_id="ali-round")
            self.feed("RespondingContent", spoken="你好，我在。", finished=True,
                      round_id="ali-round")
            self.queue.put_nowait(b"\1\0" * 14400)
            self.feed("RespondingEnded", round_id="ali-round")

    async def recv(self):
        return await self.queue.get()

    async def close(self):
        self.closed = True

    def directives(self):
        return [json.loads(x)["payload"]["input"]["directive"]
                for x in self.sent if isinstance(x, str)]


def config(**kwargs):
    return AliyunDialogConfig("test-secret", "wss://example.com/api-ws/v1/inference",
                             "test-workspace", "test-app", **kwargs)


async def test_protocol_audio_dedup_completion_and_cancellation(monkeypatch):
    socket = AliSocket()
    monkeypatch.setattr(aliyun_dialog, "connect", AsyncMock(return_value=socket))
    backend = await AliyunDialogBackend.open(config())
    generation = backend.begin_turn("turn-1")
    await backend.send_audio(b"\0\0" * 320, generation=generation)
    await backend.end_input(generation=generation)
    await backend.end_input(generation=generation)
    events = [e async for e in backend.events()]
    assert [e.type for e in events] == [
        "transcript_final", "endpoint", "text_final", "audio", "audio_done", "done",
    ]
    assert socket.directives().count("StopSpeech") == 1
    assert "LocalRespondingEnded" not in socket.directives()
    await backend.playback_completed()
    assert socket.directives()[-1] == "LocalRespondingEnded"
    await backend.cancel(generation=generation)
    socket.queue.put_nowait(b"\1\0" * 100)
    assert [e async for e in backend.events()] == []
    assert socket.closed and socket.directives()[-1] == "Stop"
    assert "test-secret" not in repr(config())


async def test_started_alone_does_not_allow_upload(monkeypatch):
    class NotListening(AliSocket):
        async def send(self, raw):
            self.sent.append(raw)
            self.task_id = json.loads(raw)["header"]["task_id"]
            self.feed("Started")

    socket = NotListening()
    monkeypatch.setattr(aliyun_dialog, "connect", AsyncMock(return_value=socket))
    with pytest.raises(TimeoutError):
        await AliyunDialogBackend.open(config(timeout_seconds=.03))
    assert socket.closed and not any(isinstance(x, bytes) for x in socket.sent)


async def test_malformed_output_is_redacted_and_stops_input(monkeypatch):
    socket = AliSocket()
    monkeypatch.setattr(aliyun_dialog, "connect", AsyncMock(return_value=socket))
    backend = await AliyunDialogBackend.open(config())
    backend.begin_turn("turn-1")
    socket.queue.put_nowait("test-secret")
    with pytest.raises(RealtimeProviderError, match="receive-failed") as error:
        _ = [e async for e in backend.events()]
    assert "test-secret" not in str(error.value)
    assert backend.endpoint_detected()
    await backend.close()


def enable_settings(settings):
    settings.aliyun_dialog_enabled = True
    settings.aliyun_dialog_api_key = "test-secret"
    settings.aliyun_dialog_workspace_id = "test-workspace"
    settings.aliyun_dialog_app_id = "test-app"


def test_catalog_gate_and_atomic_switch(client, admin_headers):
    user = _user_headers(client)
    agent = client.get("/v1/agents", headers=user).json()[0]
    staff = _staff_headers(client, admin_headers)
    path = "/v1/admin/model-presets/aliyun-dialog"
    assert client.patch(path, headers=staff, json={"enabled": True, "confirm": True}
                        ).status_code == 422
    enable_settings(client.app.state.settings)
    result = client.patch(path, headers=staff, json={"enabled": True, "confirm": True})
    assert result.status_code == 200, result.text
    assert result.json()["capabilities"]["system_prompt"] is False
    path = f"/v1/agents/{agent['id']}"
    assert client.patch(path, headers=user, json={"model_preset_id": "aliyun-dialog"}
                        ).status_code == 422
    result = client.patch(path, headers=user, json={
        "model_preset_id": "aliyun-dialog", "voice_preset_id": "aliyun-app-default",
    })
    assert result.status_code == 200, result.text
    assert result.json()["config_version"] == agent["config_version"] + 1
    assert result.json()["system_prompt"] == agent["system_prompt"]
    assert client.patch(path, headers=user, json={"system_prompt": "ignored persona"}
                        ).status_code == 422
    assert client.patch(path, headers=user, json={"tools": {"calculator": True}}
                        ).status_code == 422
    assert client.patch(path, headers=user, json={
        "model_preset_id": "fast-chat", "voice_preset_id": "cherry",
    }).status_code == 200


def test_gateway_uses_ali_protocol_and_records_ali_usage(client, admin_headers, monkeypatch):
    from backend.realtime import session as realtime_session

    owned = provision_owned_device(client, admin_headers)
    enable_settings(client.app.state.settings)
    socket = AliSocket()
    monkeypatch.setattr(aliyun_dialog, "connect", AsyncMock(return_value=socket))
    monkeypatch.setattr(realtime_session, "StreamingOpusToPcm", FakeDecoder)
    monkeypatch.setattr(s2s, "StreamingPcmToOpus", FakeEncoder)

    async def configure():
        async with client.app.state.session_factory() as db:
            model = await db.get(ModelPreset, "aliyun-dialog")
            model.enabled = True
            device = await db.get(Device, owned["device_id"])
            agent = await db.get(Agent, device.active_agent_id)
            agent.model_preset_id = model.id
            agent.voice_preset_id = "aliyun-app-default"
            await db.commit()

    client.portal.call(configure)
    with client.websocket_connect("/v1/device/ws", headers={
        "Device-Id": owned["serial"], "Authorization": f"Bearer {owned['device_secret']}",
    }) as device:
        device.send_json({"type": "hello", "version": 1,
                          "features": {"strict_playback_ack": True}})
        assert device.receive_json()["type"] == "hello"
        device.send_json({"type": "listen", "state": "start"})
        device.send_bytes(b"fake-opus")
        device.send_json({"type": "listen", "state": "stop"})
        _, packets = receive_one_turn(device)
    assert packets
    assert socket.directives().index("LocalRespondingEnded") < socket.directives().index("Stop")

    async def usages():
        async with client.app.state.session_factory() as db:
            return list(await db.scalars(select(ProviderUsage)))

    rows = client.portal.call(usages)
    assert len(rows) == 1
    assert rows[0].provider == "aliyun-dialog"
    assert rows[0].operation == "managed_dialog"
    assert rows[0].cost_status == "unknown"
