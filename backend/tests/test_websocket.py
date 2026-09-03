import asyncio
import json
import time
from collections.abc import AsyncIterator
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from backend.ai.context import LlmRequest
from backend.app.models import ConversationSession, DeviceSession
from backend.realtime import session as realtime_session
from backend.realtime.providers import TranscriptionResult

from .conftest import provision_owned_device


class FixedAsrSession:
    async def send_audio(self, frame: bytes) -> None:
        del frame

    async def finish(self) -> TranscriptionResult:
        return TranscriptionResult("测试多帧语音", "happy")

    async def cancel(self) -> None:
        return None


class FixedStreamingLlm:
    async def reply_stream(
        self,
        request: LlmRequest,
        *,
        tool_executor=None,
    ) -> AsyncIterator[str]:
        del request, tool_executor
        yield "多帧回复。"


class MultiFrameTtsSession:
    def __init__(self) -> None:
        self.cancelled = False

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        del text
        yield b"opus-frame-1"
        yield b"opus-frame-2"

    async def finish(self) -> None:
        return None

    async def cancel(self) -> None:
        self.cancelled = True


class MultiFrameRealtimeProviders:
    mock = True
    llm = FixedStreamingLlm()

    def __init__(self) -> None:
        self.tts_sessions: list[MultiFrameTtsSession] = []

    async def open_asr(self) -> FixedAsrSession:
        return FixedAsrSession()

    async def open_tts(
        self, voice: str, speech_rate: float = 1.0
    ) -> MultiFrameTtsSession:
        del voice, speech_rate
        session = MultiFrameTtsSession()
        self.tts_sessions.append(session)
        return session


class HistoryCapturingLlm(FixedStreamingLlm):
    def __init__(self) -> None:
        self.histories: list[list[dict[str, str]]] = []
        self.contexts: list[list[dict[str, object]]] = []

    async def reply_stream(
        self,
        request: LlmRequest,
        *,
        tool_executor=None,
    ) -> AsyncIterator[str]:
        del tool_executor
        self.contexts.append([dict(message) for message in request.context.messages])
        self.histories.append(
            [
                dict(message)
                for message in request.context.messages[1:-1]
                if message.get("role") in {"user", "assistant"}
            ]
        )
        yield "多轮回复。"


class MultiTurnProviders(MultiFrameRealtimeProviders):
    def __init__(self) -> None:
        self.llm = HistoryCapturingLlm()

    async def open_tts(
        self, voice: str, speech_rate: float = 1.0
    ) -> MultiFrameTtsSession:
        del voice, speech_rate

        class SingleFrameTtsSession(MultiFrameTtsSession):
            async def synthesize(self, text: str) -> AsyncIterator[bytes]:
                del text
                yield b"opus-frame"

        return SingleFrameTtsSession()


class ServerEndpointAsrSession(FixedAsrSession):
    def __init__(self) -> None:
        self.frames = 0

    async def send_audio(self, frame: bytes) -> None:
        del frame
        self.frames += 1

    def endpoint_detected(self) -> bool:
        return self.frames >= 2


class ServerEndpointProviders(MultiFrameRealtimeProviders):
    def __init__(self) -> None:
        super().__init__()
        self.opened_asr_sessions = 0

    async def open_asr(self) -> ServerEndpointAsrSession:
        self.opened_asr_sessions += 1
        return ServerEndpointAsrSession()


def _device_headers(owned: dict[str, str]) -> dict[str, str]:
    return {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {owned['device_secret']}",
        "Protocol-Version": "1",
    }


async def _latest_runtime_pair(client: TestClient, device_id: str):
    async with client.app.state.session_factory() as session:
        device_session = await session.scalar(
            select(DeviceSession)
            .where(DeviceSession.device_id == device_id)
            .order_by(DeviceSession.connected_at.desc())
        )
        conversation = await session.scalar(
            select(ConversationSession)
            .where(ConversationSession.device_id == device_id)
            .order_by(ConversationSession.started_at.desc())
        )
        assert device_session is not None
        return (
            device_session.heartbeat_at,
            device_session.status,
            device_session.disconnected_at,
            conversation.ended_at if conversation is not None else None,
            conversation.end_reason if conversation is not None else None,
        )


async def _conversation_records(client: TestClient, device_id: str):
    async with client.app.state.session_factory() as session:
        return list(
            await session.scalars(
                select(ConversationSession)
                .where(ConversationSession.device_id == device_id)
                .order_by(ConversationSession.started_at)
            )
        )


def _receive_audio_until_stop(websocket) -> tuple[bytes, dict[str, object]]:
    audio_parts: list[bytes] = []
    sentence_count = 0
    while True:
        message = websocket.receive()
        if message.get("bytes") is not None:
            audio_parts.append(message["bytes"])
            continue
        payload = json.loads(message["text"])
        if payload.get("type") == "tts" and payload.get("state") == "sentence_start":
            sentence_count += 1
            continue
        if payload.get("type") == "tts" and payload.get("state") == "stop":
            assert sentence_count >= 1
            assert audio_parts
            return b"".join(audio_parts), payload


def _receive_mock_turn(websocket) -> tuple[dict[str, object], bytes]:
    stt = websocket.receive_json()
    assert stt["type"] == "stt"
    thinking = websocket.receive_json()
    assert thinking["type"] == "llm"
    start = websocket.receive_json()
    assert start["state"] == "start"
    assert start["reply_id"]
    assert start["turn_id"] == stt["turn_id"]
    assert stt["turn_id"] == thinking["turn_id"]
    websocket.send_json({"type": "tts", "state": "ready", "reply_id": start["reply_id"]})
    audio, stop = _receive_audio_until_stop(websocket)
    assert stop["type"] == "tts"
    assert stop["state"] == "stop"
    assert stop["reply_id"] == start["reply_id"]
    assert stop["turn_id"] == start["turn_id"]
    websocket.send_json({"type": "tts", "state": "drained", "reply_id": start["reply_id"]})
    completed = websocket.receive_json()
    assert completed == {
        "session_id": start["session_id"],
        "type": "turn",
        "state": "completed",
        "turn_id": start["turn_id"],
        "reply_id": start["reply_id"],
    }
    return stt, audio


def test_mock_voice_turn_uses_xiaozhi_message_shapes(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        hello = websocket.receive_json()
        assert hello["type"] == "hello"
        assert hello["audio_params"]["format"] == "mock-utf8"
        assert "AI" in hello["disclosure"]
        assert hello["session_id"]

        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("今天有什么安排".encode())
        websocket.send_json({"type": "listen", "state": "stop"})

        stt, audio = _receive_mock_turn(websocket)
        assert stt["text"] == "今天有什么安排"
        assert audio.decode() == "收到：今天有什么安排"

    entitlement = client.get(
        "/v1/account/entitlement",
        headers={"Authorization": f"Bearer {owned['user_token']}"},
    )
    assert entitlement.json()["used_turns"] == 1
    assert entitlement.json()["remaining_turns"] == 599


def test_device_stage_events_are_bounded_diagnostics(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-STAGE-EVENTS")
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"
        for stage in ("capture_started", "speaker_pcm_started", "playback_drained"):
            websocket.send_json(
                {
                    "type": "device_stage",
                    "stage": stage,
                    "turn_id": "turn-stage-test",
                    "reply_id": "reply-stage-test",
                }
            )
        websocket.send_json({"type": "ping", "sequence": 7})
        pong = websocket.receive_json()
        assert pong["type"] == "pong"
        assert pong["sequence"] == 7

        websocket.send_json({"type": "device_stage", "stage": "transcript_captured"})
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid-device-stage"


def test_strict_playback_ready_timeout_aborts_before_audio(
    client: TestClient, admin_headers: dict[str, str], caplog
) -> None:
    caplog.set_level("INFO")
    providers = MultiFrameRealtimeProviders()
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-STRICT-READY")
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "version": 1,
                "features": {"strict_playback_ack": True},
            }
        )
        assert websocket.receive_json()["type"] == "hello"
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("测试严格握手".encode())
        websocket.send_json({"type": "listen", "state": "stop"})
        assert websocket.receive_json()["type"] == "stt"
        assert websocket.receive_json()["type"] == "llm"
        assert websocket.receive_json()["state"] == "start"
        stop = websocket.receive_json()
        assert stop["type"] == "tts"
        assert stop["state"] == "stop"
        assert stop["turn_id"]
        assert stop["reply_id"]
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "tts-ready-timeout"

    assert not client.app.state.device_connections._connections
    assert len(providers.tts_sessions) == 1
    assert providers.tts_sessions[0].cancelled
    outcome_messages = [
        record.getMessage()
        for record in caplog.records
        if "voice turn outcome " in record.getMessage()
    ]
    assert len(outcome_messages) == 1
    outcome = json.loads(outcome_messages[0].split("voice turn outcome ", 1)[1])
    assert outcome["outcome"] == "failed"
    assert outcome["error_code"] == "tts-ready-timeout"
    assert outcome["device_speaker_started_ms"] is None


def test_strict_playback_drained_timeout_reports_stable_error(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-STRICT-DRAINED")
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json(
            {
                "type": "hello",
                "version": 1,
                "features": {"strict_playback_ack": True},
            }
        )
        assert websocket.receive_json()["type"] == "hello"
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("测试播放完成".encode())
        websocket.send_json({"type": "listen", "state": "stop"})
        assert websocket.receive_json()["type"] == "stt"
        assert websocket.receive_json()["type"] == "llm"
        start = websocket.receive_json()
        assert start["state"] == "start"
        websocket.send_json(
            {
                "type": "tts",
                "state": "ready",
                "turn_id": start["turn_id"],
                "reply_id": start["reply_id"],
            }
        )
        _, stop = _receive_audio_until_stop(websocket)
        assert stop["state"] == "stop"
        error = websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "tts-drained-timeout"


def test_admin_can_push_whitelisted_face_events_to_an_online_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        hello = websocket.receive_json()

        sent = client.post(
            f"/v1/admin/device-events/{owned['serial']}",
            headers=admin_headers,
            json={
                "event": "reminder",
                "message_type": "alert",
                "status": "提醒",
                "message": "该休息一下了",
            },
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["delivered"] is True
        event = websocket.receive_json()
        assert event == {
            "session_id": hello["session_id"],
            "type": "alert",
            "status": "提醒",
            "message": "该休息一下了",
            "emotion": "reminder",
        }

        sent = client.post(
            f"/v1/admin/device-events/{owned['serial']}",
            headers=admin_headers,
            json={"event": "happy"},
        )
        assert sent.status_code == 200, sent.text
        event = websocket.receive_json()
        assert event["type"] == "llm"
        assert event["emotion"] == "happy"
        assert event["session_id"] == hello["session_id"]


def test_device_receives_each_streamed_audio_frame(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = MultiFrameRealtimeProviders()
    owned = provision_owned_device(client, admin_headers)
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["audio_params"]["format"] == "mock-utf8"
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"input-opus")
        websocket.send_json({"type": "listen", "state": "stop"})

        assert websocket.receive_json()["type"] == "stt"
        assert websocket.receive_json()["type"] == "llm"
        start = websocket.receive_json()
        assert start["state"] == "start"
        websocket.send_json(
            {"type": "tts", "state": "ready", "reply_id": start["reply_id"]}
        )
        assert websocket.receive_json()["state"] == "sentence_start"
        assert websocket.receive_bytes() == b"opus-frame-1"
        reply_emotion = websocket.receive_json()
        assert reply_emotion["type"] == "llm"
        assert reply_emotion["emotion"] == "happy"
        assert websocket.receive_bytes() == b"opus-frame-2"
        stop = websocket.receive_json()
        assert stop["state"] == "stop"
        assert stop["reply_id"] == start["reply_id"]
        websocket.send_json(
            {"type": "tts", "state": "drained", "reply_id": start["reply_id"]}
        )
        assert websocket.receive_json()["state"] == "completed"


def test_same_websocket_accepts_a_followup_turn_with_history(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    providers = MultiTurnProviders()
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-MULTI-TURN")

    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"

        for payload in (b"first-turn", b"second-turn"):
            websocket.send_json({"type": "listen", "state": "start"})
            websocket.send_bytes(payload)
            websocket.send_json({"type": "listen", "state": "stop"})
            _receive_mock_turn(websocket)
            # A real Hensun waits for its post-playback guard before opening
            # the microphone; allow the acknowledged turn task to settle too.
            time.sleep(0.05)

    assert providers.llm.histories[0] == []
    assert providers.llm.histories[1] == [
        {"role": "user", "content": "测试多帧语音"},
        {"role": "assistant", "content": "多轮回复。"},
    ]


def test_idle_timeout_ends_logical_conversation_without_closing_websocket(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.realtime_providers = MultiTurnProviders()
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-LOGICAL-SESSIONS")

    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"

        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"first-session")
        websocket.send_json({"type": "listen", "state": "stop"})
        _receive_mock_turn(websocket)
        websocket.send_json({"type": "abort", "reason": "idle_timeout"})
        assert websocket.receive_json()["state"] == "aborted"

        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"second-session")
        websocket.send_json({"type": "listen", "state": "stop"})
        _receive_mock_turn(websocket)
        websocket.send_json({"type": "abort", "reason": "test-complete"})
        assert websocket.receive_json()["state"] == "aborted"

    deadline = time.monotonic() + 1.0
    while True:
        records = asyncio.run(_conversation_records(client, owned["device_id"]))
        complete = len(records) == 2 and records[1].ended_at is not None
        if complete or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert len(records) == 2
    assert records[0].ended_at is not None
    assert records[0].end_reason == "idle_timeout"
    assert records[1].ended_at is not None
    assert records[1].end_reason == "test-complete"


def test_new_logical_conversation_loads_summary_without_reconnecting_websocket(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    providers = MultiTurnProviders()
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-SUMMARY-RELOAD")
    user_headers = {"Authorization": f"Bearer {owned['user_token']}"}
    agent = client.get("/v1/agents", headers=user_headers).json()[0]
    assert client.patch(
        f"/v1/agents/{agent['id']}",
        headers=user_headers,
        json={"memory_consent": True},
    ).status_code == 200

    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"

        for reason in ("idle_timeout", "test-complete"):
            websocket.send_json({"type": "listen", "state": "start"})
            websocket.send_bytes(b"remember-this")
            websocket.send_json({"type": "listen", "state": "stop"})
            _receive_mock_turn(websocket)
            websocket.send_json({"type": "abort", "reason": reason})
            assert websocket.receive_json()["state"] == "aborted"
            if reason != "idle_timeout":
                assert websocket.receive_json()["emotion"] == "interrupted"

    main_contexts = [
        context
        for context in providers.llm.contexts
        if context[-1].get("content") == "测试多帧语音"
    ]
    assert len(main_contexts) == 2
    assert not any("最近会话摘要" in str(message.get("content")) for message in main_contexts[0])
    assert any("最近会话摘要" in str(message.get("content")) for message in main_contexts[1])


def test_server_vad_finishes_turn_without_device_listen_stop(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    providers = ServerEndpointProviders()
    client.app.state.realtime_providers = providers
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-SERVER-VAD")
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes(b"first-frame")
        websocket.send_bytes(b"second-frame")

        assert websocket.receive_json()["type"] == "stt"
        assert websocket.receive_json()["type"] == "llm"
        start = websocket.receive_json()
        assert start["state"] == "start"
        websocket.send_json(
            {"type": "tts", "state": "ready", "reply_id": start["reply_id"]}
        )
        assert websocket.receive_json()["state"] == "sentence_start"
        assert websocket.receive_bytes() == b"opus-frame-1"
        reply_emotion = websocket.receive_json()
        assert reply_emotion["type"] == "llm"
        assert reply_emotion["emotion"] == "happy"
        assert websocket.receive_bytes() == b"opus-frame-2"
        stop = websocket.receive_json()
        websocket.send_json(
            {"type": "tts", "state": "drained", "reply_id": stop["reply_id"]}
        )
        assert websocket.receive_json()["state"] == "completed"

    assert providers.opened_asr_sessions == 1


def test_face_event_api_rejects_unknown_events_and_queues_offline_devices(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers)
    unknown = client.post(
        f"/v1/admin/device-events/{owned['serial']}",
        headers=admin_headers,
        json={"event": "arbitrary-servo-command"},
    )
    assert unknown.status_code == 422

    offline = client.post(
        f"/v1/admin/device-events/{owned['serial']}",
        headers=admin_headers,
        json={"event": "happy"},
    )
    assert offline.status_code == 200
    assert offline.json()["delivered"] is False
    assert offline.json()["queued"] is True


def test_xiaozhi_bootstrap_token_can_open_device_websocket(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="02:00:00:00:00:01")
    bootstrap = client.post(
        "/v1/device/xiaozhi-bootstrap",
        headers={"Device-Id": owned["serial"], "Client-Id": "local-pilot"},
        json={"application": {"version": "2.4.2-local"}},
    )
    assert bootstrap.status_code == 200, bootstrap.text
    configuration = bootstrap.json()
    assert configuration["websocket"]["url"].endswith("/v1/device/ws")
    assert configuration["websocket"]["version"] == 1
    assert configuration["server_time"]["timezone_offset"] == 0

    headers = {
        "Device-Id": owned["serial"],
        "Authorization": f"Bearer {configuration['websocket']['token']}",
        "Protocol-Version": "1",
    }
    with client.websocket_connect("/v1/device/ws", headers=headers) as websocket:
        websocket.send_json({"type": "hello", "version": 1, "transport": "websocket"})
        assert websocket.receive_json()["type"] == "hello"


def test_device_ping_updates_persisted_heartbeat(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-HEARTBEAT")
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"
        before = asyncio.run(_latest_runtime_pair(client, owned["device_id"]))[0]

        time.sleep(0.01)
        websocket.send_json({"type": "ping", "sequence": 7})
        assert websocket.receive_json() == {
            "session_id": ANY,
            "type": "pong",
            "sequence": 7,
        }
        after = asyncio.run(_latest_runtime_pair(client, owned["device_id"]))[0]

    assert after > before


def test_silent_device_receive_returns_timeout_without_preempting_cleanup() -> None:
    class SilentWebSocket:
        def __init__(self) -> None:
            self.closed: tuple[int, str] | None = None

        async def receive(self):
            await asyncio.Event().wait()

        async def close(self, *, code: int, reason: str) -> None:
            self.closed = (code, reason)

    websocket = SilentWebSocket()
    incoming = asyncio.run(
        realtime_session._receive_device_message(websocket, timeout_seconds=0.01)
    )

    assert incoming is None
    assert websocket.closed is None


def test_silent_device_timeout_closes_runtime_records(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.app.state.settings.device_ws_activity_timeout_seconds = 0.05
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-IDLE-TIMEOUT")

    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"
        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_json()
        assert excinfo.value.code == 1001

    deadline = time.monotonic() + 1.0
    while True:
        runtime = asyncio.run(_latest_runtime_pair(client, owned["device_id"]))
        if runtime[1] == "offline" or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    _, status, disconnected_at, ended_at, end_reason = runtime
    assert status == "offline"
    assert disconnected_at is not None
    assert ended_at is None
    assert end_reason is None


def test_xiaozhi_bootstrap_returns_six_digit_claim_code_for_unclaimed_device(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    registered = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": "02:00:00:00:00:02", "board_type": "hensun-cam-pilot-v1"},
    )
    assert registered.status_code == 200
    bootstrap = client.post(
        "/v1/device/xiaozhi-bootstrap",
        headers={"Device-Id": "02:00:00:00:00:02"},
        json={},
    )
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json()["activation"]["code"].isdigit()
    assert len(bootstrap.json()["activation"]["code"]) == 6
    assert bootstrap.json()["activation"]["timeout_ms"] == 600_000
    assert bootstrap.json()["activation"]["claim_url"] == (
        client.app.state.settings.web_app_url.rstrip("/") + "/claim#code="
        + bootstrap.json()["activation"]["code"]
    )


def test_xiaocan_shut_up_enters_standby_without_goodbye(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-XIAOCAN-EXIT")
    with client.websocket_connect("/v1/device/ws", headers=_device_headers(owned)) as websocket:
        websocket.send_json({"type": "hello", "version": 1})
        assert websocket.receive_json()["type"] == "hello"

        websocket.send_json({"type": "listen", "state": "start"})
        websocket.send_bytes("小灿闭嘴".encode())
        websocket.send_json({"type": "listen", "state": "stop"})

        stt = websocket.receive_json()
        assert stt["type"] == "stt"
        assert stt["text"] == "小灿闭嘴"

        standby = websocket.receive_json()
        assert standby == {
            "session_id": ANY,
            "type": "listen",
            "state": "standby",
            "reason": "user-exit",
            "turn_id": stt["turn_id"],
        }

        websocket.send_json({"type": "ping", "sequence": 1})
        assert websocket.receive_json() == {
            "session_id": ANY,
            "type": "pong",
            "sequence": 1,
        }

    deadline = time.monotonic() + 1.0
    while True:
        runtime = asyncio.run(_latest_runtime_pair(client, owned["device_id"]))
        if runtime[4] == "user-exit" or time.monotonic() >= deadline:
            break
        time.sleep(0.01)
    assert runtime[4] == "user-exit"
