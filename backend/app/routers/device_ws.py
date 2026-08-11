import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..audit import add_audit_event
from ..models import Device, DeviceLifecycle, MemorySummary, UsageEvent
from ..quota import quota_for_user
from ..safety import evaluate_text
from ..security import decrypt_memory, verify_device_session_token, verify_secret

router = APIRouter(tags=["device-websocket"])
logger = logging.getLogger(__name__)

MAX_UTTERANCE_BYTES = 1024 * 1024


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    await websocket.send_json({"type": "error", "code": code, "message": message})


def _emotion_for_safety_category(category: str | None) -> str:
    if category == "self-harm":
        return "caring"
    if category == "user-exit":
        return "apology"
    return "happy"


@router.websocket("/v1/device/ws")
async def device_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    serial = websocket.headers.get("device-id", "")
    authorization = websocket.headers.get("authorization", "")
    secret = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""

    async with websocket.app.state.session_factory() as session:
        device = await session.scalar(select(Device).where(Device.serial_number == serial))
        valid_device_secret = device is not None and verify_secret(
            secret, device.credential_hash, settings.device_credential_pepper
        )
        valid_session_token = verify_device_session_token(secret, serial, settings)
        if device is None or not (valid_device_secret or valid_session_token):
            await websocket.close(code=4401, reason="invalid device credential")
            return
        if device.lifecycle != DeviceLifecycle.OWNED.value or not device.owner_user_id:
            await websocket.close(code=4403, reason="device is not active and owned")
            return

        await websocket.accept()
        await websocket.app.state.device_connections.connect(serial, websocket)
        session_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        audio_frames: list[bytes] = []
        audio_bytes = 0
        device.last_seen_at = started_at
        await session.commit()

        try:
            while True:
                incoming = await websocket.receive()
                if incoming.get("type") == "websocket.disconnect":
                    return
                if incoming.get("bytes") is not None:
                    chunk = incoming["bytes"]
                    if audio_bytes + len(chunk) > MAX_UTTERANCE_BYTES:
                        audio_frames.clear()
                        audio_bytes = 0
                        await _send_error(websocket, "audio-too-large", "utterance exceeds 1 MiB")
                        continue
                    audio_frames.append(chunk)
                    audio_bytes += len(chunk)
                    continue

                text_frame = incoming.get("text")
                if text_frame is None:
                    continue
                try:
                    message = json.loads(text_frame)
                except json.JSONDecodeError:
                    await _send_error(websocket, "invalid-json", "control message must be JSON")
                    continue

                message_type = message.get("type")
                if message_type == "hello":
                    await websocket.send_json(
                        {
                            "type": "hello",
                            "transport": "websocket",
                            "version": 1,
                            "session_id": session_id,
                            "audio_params": {
                                "format": websocket.app.state.providers.audio_codec,
                                "sample_rate": 24000,
                                "channels": 1,
                                "frame_duration": 60,
                            },
                            "disclosure": "你正在与 AI 服务互动，而非自然人。",
                        }
                    )
                    continue
                if message_type == "abort":
                    audio_frames.clear()
                    audio_bytes = 0
                    await websocket.send_json({"type": "system", "state": "aborted"})
                    continue
                if message_type != "listen":
                    await _send_error(
                        websocket, "unsupported-message", "unsupported control message"
                    )
                    continue
                if message.get("state") == "detect":
                    audio_frames.clear()
                    audio_bytes = 0
                    continue
                if message.get("state") == "start":
                    audio_frames.clear()
                    audio_bytes = 0
                    continue
                if message.get("state") != "stop":
                    await _send_error(
                        websocket, "invalid-listen-state", "listen state must be start or stop"
                    )
                    continue
                if not audio_frames:
                    logger.warning("device %s stopped listening without audio", serial)
                    await _send_error(websocket, "empty-audio", "no audio received")
                    continue

                logger.info(
                    "device %s submitted %d audio frames (%d bytes)",
                    serial,
                    len(audio_frames),
                    audio_bytes,
                )

                quota = await quota_for_user(session, device.owner_user_id, settings)
                if quota.remaining <= 0:
                    audio_frames.clear()
                    audio_bytes = 0
                    await _send_error(websocket, "quota-exhausted", "monthly voice quota exhausted")
                    continue

                providers = websocket.app.state.providers
                try:
                    transcript = await providers.speech.transcribe(audio_frames)
                except Exception:
                    audio_frames.clear()
                    audio_bytes = 0
                    await _send_error(
                        websocket, "asr-unavailable", "speech recognition unavailable"
                    )
                    continue
                audio_frames.clear()
                audio_bytes = 0
                if not transcript:
                    await _send_error(websocket, "empty-transcript", "speech was not recognized")
                    continue

                await websocket.send_json(
                    {"type": "stt", "session_id": session_id, "text": transcript}
                )
                decision = evaluate_text(transcript)
                memories: list[str] = []
                if device.memory_consent:
                    encrypted_memories = list(
                        await session.scalars(
                            select(MemorySummary.encrypted_value).where(
                                MemorySummary.device_id == device.id,
                                MemorySummary.user_id == device.owner_user_id,
                            )
                        )
                    )
                    memories = [decrypt_memory(item, settings) for item in encrypted_memories]

                try:
                    reply = decision.fixed_response or await providers.llm.reply(
                        transcript, memories
                    )
                    speech = await providers.speech.synthesize(reply)
                except Exception:
                    await _send_error(websocket, "ai-unavailable", "AI response unavailable")
                    continue

                await websocket.send_json(
                    {
                        "type": "llm",
                        "session_id": session_id,
                        "text": reply,
                        "emotion": _emotion_for_safety_category(decision.category),
                    }
                )
                await websocket.send_json({"type": "tts", "state": "start"})
                for frame in speech:
                    await websocket.send_bytes(frame)

                session.add(
                    UsageEvent(
                        user_id=device.owner_user_id,
                        device_id=device.id,
                        kind="voice-turn",
                        quantity=1,
                    )
                )
                add_audit_event(
                    session,
                    actor_type="device",
                    actor_id=device.id,
                    action="voice.turn-completed",
                    payload={"safety_category": decision.category},
                )
                await session.commit()
                await websocket.send_json({"type": "tts", "state": "stop"})

                elapsed = datetime.now(UTC) - started_at
                if elapsed.total_seconds() >= 7200:
                    await websocket.send_json(
                        {
                            "type": "alert",
                            "status": "休息提醒",
                            "message": "你已经连续使用超过两小时，建议休息一下。",
                            "emotion": "reminder",
                        }
                    )
                if decision.end_session:
                    await websocket.close(code=1000, reason="user requested exit")
                    return
        except WebSocketDisconnect:
            return
        finally:
            await websocket.app.state.device_connections.disconnect(serial, websocket)
