import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models import (
    ConversationSession,
    Device,
    DeviceLifecycle,
    DeviceSession,
    DeviceSessionStatus,
    UsageProfile,
)
from backend.app.profile_policy import evaluate_profile_policy
from backend.app.security import verify_device_session_token, verify_secret
from backend.generated.device_contracts import (
    DEVICE_CONFIG_SCHEMA_VERSION,
    DEVICE_WS_PROTOCOL_VERSION,
)

from .device_state import handle_device_config_ack, heartbeat, record_device_hello
from .mcp import DeviceMcpClient, DeviceMcpError
from .messaging import send_error
from .playback import PlaybackHandshake
from .providers import RealtimeAsrSession, open_asr_for
from .snapshot import load_snapshot
from .summaries import save_session_summary
from .turn import SentenceBuffer, process_turn, speak_fixed_message
from .turn_state import VoiceTurnState, VoiceTurnStateMachine

logger = logging.getLogger(__name__)
MAX_UTTERANCE_BYTES = 1024 * 1024

# Re-exported for existing callers while the implementation lives with turn orchestration.
__all__ = ["SentenceBuffer", "serve_device_websocket"]


async def serve_device_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    gateway_settings = settings.gateway
    serial = websocket.headers.get("device-id", "")
    authorization = websocket.headers.get("authorization", "")
    secret = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory

    async with session_factory() as session:
        device = await session.scalar(select(Device).where(Device.serial_number == serial))
        valid_secret = device is not None and verify_secret(
            secret, device.credential_hash, settings.security.device_credential_pepper
        )
        valid_token = verify_device_session_token(secret, serial, settings)
        if device is None or not (valid_secret or valid_token):
            await websocket.close(code=4401, reason="invalid device credential")
            return
        if device.lifecycle != DeviceLifecycle.OWNED.value or not device.owner_user_id:
            await websocket.close(code=4403, reason="device is not active and owned")
            return
        snapshot = await load_snapshot(session, device, settings)
        await session.flush()
        connection_id = str(uuid.uuid4())
        conversation = ConversationSession(
            user_id=device.owner_user_id,
            agent_id=snapshot.agent_id,
            device_id=device.id,
            usage_profile_id=snapshot.usage_profile_id,
        )
        device_session = DeviceSession(
            device_id=device.id,
            gateway_id=gateway_settings.gateway_id,
            connection_id=connection_id,
            firmware_version=device.firmware_version,
        )
        session.add_all([conversation, device_session])
        device.last_seen_at = datetime.now(UTC)
        await session.commit()
        device_id = device.id
        user_id = device.owner_user_id
        conversation_id = conversation.id
        conversation_started_at = conversation.started_at
        device_session_id = device_session.id

    await websocket.accept()
    await websocket.app.state.device_connections.connect(serial, websocket, connection_id)
    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        heartbeat(session_factory, device_session_id, heartbeat_stop)
    )
    active_asr: RealtimeAsrSession | None = None
    active_task: asyncio.Task[bool] | None = None
    audio_bytes = 0
    audio_frames = 0
    audio_buffer: list[bytes] = []
    history: list[dict[str, str]] = []
    end_reason = "disconnected"
    connected_at = time.perf_counter()
    cancelled = False
    continuous_reminder_sent = False
    playback = PlaybackHandshake()
    turn_state = VoiceTurnStateMachine()
    mcp_client: DeviceMcpClient | None = None
    mcp_initialize_task: asyncio.Task[None] | None = None

    def asr_endpoint_detected(asr: RealtimeAsrSession) -> bool:
        detector = getattr(asr, "endpoint_detected", None)
        return bool(detector and detector())

    def start_active_turn() -> bool:
        nonlocal active_asr, active_task, audio_bytes, audio_frames, audio_buffer
        if active_asr is None or audio_bytes == 0:
            return False
        turn_asr = active_asr
        turn_audio_duration_ms = audio_frames * 60
        turn_audio_frames = audio_buffer.copy()
        active_asr = None
        audio_bytes = 0
        audio_frames = 0
        audio_buffer.clear()
        turn_state.transition(VoiceTurnState.RECOGNIZING)
        active_task = asyncio.create_task(
            process_turn(
                websocket,
                serial,
                device_id,
                user_id,
                conversation_id,
                snapshot,
                turn_asr,
                turn_audio_frames,
                turn_audio_duration_ms,
                history,
                time.perf_counter(),
                playback,
                mcp_client,
                turn_state,
            )
        )
        return True

    try:
        while True:
            incoming = await websocket.receive()
            if incoming.get("type") == "websocket.disconnect":
                break
            chunk = incoming.get("bytes")
            if chunk is not None:
                # A server-VAD endpoint may arrive while local VAD is still in speech.
                if active_task is not None and not active_task.done():
                    continue
                if active_asr is None:
                    logger.warning(
                        "audio arrived before listen.start for %s; opening ASR implicitly", serial
                    )
                    if turn_state.state == VoiceTurnState.IDLE:
                        turn_state.transition(VoiceTurnState.LISTENING)
                    active_asr = await open_asr_for(
                        websocket.app.state.realtime_providers,
                        snapshot.asr_provider,
                        snapshot.asr_model,
                    )
                    audio_bytes = 0
                    audio_frames = 0
                    audio_buffer.clear()
                if audio_bytes + len(chunk) > MAX_UTTERANCE_BYTES:
                    await active_asr.cancel()
                    active_asr = None
                    audio_bytes = 0
                    audio_frames = 0
                    audio_buffer.clear()
                    turn_state.reset()
                    await send_error(
                        websocket, serial, "audio-too-large", "utterance exceeds 1 MiB"
                    )
                    continue
                if audio_frames >= gateway_settings.max_device_audio_queue_frames:
                    await active_asr.cancel()
                    active_asr = None
                    audio_bytes = 0
                    audio_frames = 0
                    audio_buffer.clear()
                    turn_state.reset()
                    await send_error(
                        websocket,
                        serial,
                        "audio-frame-limit",
                        "utterance exceeds the configured frame limit",
                    )
                    continue
                await active_asr.send_audio(chunk)
                audio_bytes += len(chunk)
                audio_frames += 1
                audio_buffer.append(bytes(chunk))
                if asr_endpoint_detected(active_asr):
                    start_active_turn()
                continue

            text_frame = incoming.get("text")
            if text_frame is None:
                continue
            try:
                message = json.loads(text_frame)
            except json.JSONDecodeError:
                await send_error(websocket, serial, "invalid-json", "control message must be JSON")
                continue
            message_type = message.get("type")
            if message_type == "hello":
                await record_device_hello(session_factory, device_id, message)
                await websocket.app.state.device_connections.send_json(
                    serial,
                    {
                        "type": "hello",
                        "transport": "websocket",
                        "version": 1,
                        "protocol_version": DEVICE_WS_PROTOCOL_VERSION,
                        "device_config_schema_version": DEVICE_CONFIG_SCHEMA_VERSION,
                        "audio_params": {
                            "format": "mock-utf8"
                            if websocket.app.state.realtime_providers.mock
                            else "opus",
                            "sample_rate": 24000,
                            "channels": 1,
                            "frame_duration": 60,
                        },
                        "features": {"mcp": True},
                        "disclosure": "你正在与 AI 服务互动，而非自然人。",
                    },
                )
                if not websocket.app.state.realtime_providers.mock:
                    mcp_client = DeviceMcpClient(
                        serial, websocket.app.state.device_connections
                    )
                    mcp_initialize_task = asyncio.create_task(mcp_client.initialize())
                continue
            if message_type == "abort":
                turn_state.cancel()
                if active_asr is not None:
                    await active_asr.cancel()
                    active_asr = None
                if active_task is not None and not active_task.done():
                    active_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await active_task
                audio_bytes = 0
                audio_frames = 0
                audio_buffer.clear()
                playback.clear()
                turn_state.reset()
                await websocket.app.state.device_connections.send_json(
                    serial, {"type": "system", "state": "aborted"}
                )
                await websocket.app.state.device_connections.send_json(
                    serial, {"type": "llm", "emotion": "interrupted"}
                )
                continue
            if message_type == "tts":
                state = str(message.get("state") or "")
                reply_id = str(message.get("reply_id") or "")
                if not playback.acknowledge(state, reply_id):
                    logger.info("ignored stale TTS %s acknowledgement from %s", state, serial)
                continue
            if message_type == "mcp":
                if mcp_client is not None and mcp_client.handle_message(message):
                    continue
                logger.info("ignored unsolicited MCP message from %s", serial)
                continue
            if message_type == "device_config_ack":
                await handle_device_config_ack(session_factory, device_id, message)
                continue
            if message_type != "listen":
                logger.info("ignored unknown device message type %r from %s", message_type, serial)
                await send_error(
                    websocket, serial, "unsupported-message", "unsupported control message"
                )
                continue
            state = message.get("state")
            if state == "detect":
                continue
            if state == "start":
                if active_task is not None and not active_task.done():
                    await send_error(
                        websocket, serial, "turn-busy", "previous turn is still active"
                    )
                    continue
                if active_task is not None:
                    try:
                        end_session = active_task.result()
                    except Exception:
                        logger.exception("completed turn task failed for device %s", serial)
                        end_session = False
                    active_task = None
                    if end_session:
                        end_reason = "user-exit"
                        await websocket.close(code=1000, reason="user requested exit")
                        break
                if turn_state.state == VoiceTurnState.IDLE:
                    turn_state.transition(VoiceTurnState.LISTENING)
                async with session_factory() as session:
                    current_device = await session.get(Device, device_id)
                    if current_device is None:
                        await websocket.close(code=4404, reason="device removed")
                        break
                    snapshot = await load_snapshot(session, current_device, settings)
                    profile = await session.get(UsageProfile, snapshot.usage_profile_id)
                    if profile is None:
                        await websocket.close(code=4404, reason="usage profile removed")
                        break
                    policy = await evaluate_profile_policy(
                        session,
                        profile,
                        family_mode_enabled=settings.family_mode_enabled,
                        conversation_started_at=conversation_started_at,
                    )
                    await session.commit()
                if not policy.allowed:
                    await websocket.app.state.device_connections.send_json(
                        serial,
                        {
                            "type": "alert",
                            "status": policy.code,
                            "message": policy.message,
                        },
                    )
                    await websocket.app.state.device_connections.send_json(
                        serial, {"type": "llm", "emotion": "safe_block"}
                    )
                    active_task = asyncio.create_task(
                        speak_fixed_message(
                            websocket,
                            serial,
                            snapshot.voice,
                            snapshot.tts_speech_rate,
                            policy.message,
                            playback,
                            tts_provider=snapshot.tts_provider,
                            tts_model=snapshot.tts_model,
                            turn_state=turn_state,
                        )
                    )
                    continue
                if policy.continuous_reminder_due and not continuous_reminder_sent:
                    reminder = "已经聊了一会儿，起来活动一下吧。"
                    await websocket.app.state.device_connections.send_json(
                        serial,
                        {
                            "type": "alert",
                            "status": "break-reminder",
                            "message": reminder,
                        },
                    )
                    active_task = asyncio.create_task(
                        speak_fixed_message(
                            websocket,
                            serial,
                            snapshot.voice,
                            snapshot.tts_speech_rate,
                            reminder,
                            playback,
                            tts_provider=snapshot.tts_provider,
                            tts_model=snapshot.tts_model,
                            turn_state=turn_state,
                        )
                    )
                    continuous_reminder_sent = True
                    continue
                # Preserve a first frame that races ahead of listen.start.
                if active_asr is None:
                    active_asr = await open_asr_for(
                        websocket.app.state.realtime_providers,
                        snapshot.asr_provider,
                        snapshot.asr_model,
                    )
                    audio_bytes = 0
                    audio_frames = 0
                    audio_buffer.clear()
                continue
            if state != "stop":
                await send_error(
                    websocket, serial, "invalid-listen-state", "listen state must be start or stop"
                )
                continue
            if active_asr is None or audio_bytes == 0:
                if active_task is not None:
                    # Server VAD already closed this utterance; local stop is an acknowledgement.
                    continue
                await send_error(websocket, serial, "empty-audio", "no audio received")
                continue
            start_active_turn()

            if time.perf_counter() - connected_at >= 7200:
                await websocket.app.state.device_connections.send_json(
                    serial,
                    {
                        "type": "alert",
                        "status": "休息提醒",
                        "message": "你已经连续使用超过两小时，建议休息一下。",
                        "emotion": "reminder",
                    },
                )
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        cancelled = True
    finally:
        if active_asr is not None:
            await active_asr.cancel()
        if active_task is not None and not active_task.done():
            active_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await active_task
        if mcp_initialize_task is not None:
            if not mcp_initialize_task.done():
                mcp_initialize_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, DeviceMcpError):
                await mcp_initialize_task
        if mcp_client is not None:
            await mcp_client.close()
        heartbeat_stop.set()
        await heartbeat_task
        await save_session_summary(websocket, conversation_id, user_id, snapshot, history)
        now = datetime.now(UTC)
        async with session_factory() as session:
            stored_device_session = await session.get(DeviceSession, device_session_id)
            if stored_device_session is not None:
                stored_device_session.status = DeviceSessionStatus.OFFLINE.value
                stored_device_session.disconnected_at = now
                stored_device_session.heartbeat_at = now
            stored_conversation = await session.get(ConversationSession, conversation_id)
            if stored_conversation is not None:
                stored_conversation.ended_at = now
                stored_conversation.end_reason = end_reason
            await session.commit()
        turn_state.reset()
        await websocket.app.state.device_connections.disconnect(serial, websocket)
    if cancelled:
        raise asyncio.CancelledError
