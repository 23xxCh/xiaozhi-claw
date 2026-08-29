import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.audit import add_audit_event
from backend.app.catalog import ensure_default_agent
from backend.app.device_connections import ConnectionLease
from backend.app.models import (
    Agent,
    AgentMemory,
    ConversationSession,
    Device,
    DeviceCommand,
    DeviceCommandStatus,
    DeviceConfiguration,
    DeviceLifecycle,
    DeviceSession,
    DeviceSessionStatus,
    EncryptedSessionSummary,
    ModelPreset,
    ProviderUsage,
    UsageEvent,
    UsageProfile,
    UsageProfileKind,
    User,
    VoicePreset,
)
from backend.app.profile_policy import evaluate_profile_policy
from backend.app.providers import ProviderBundle
from backend.app.quota import quota_for_user
from backend.app.safety import evaluate_text
from backend.app.security import (
    decrypt_memory,
    encrypt_memory,
    verify_device_session_token,
    verify_secret,
)

from .emotion import EmotionRouter
from .face_control import SUPPORTED_FACE_EMOTIONS, FaceControlEvent, FaceControlParser
from .mcp import DeviceMcpClient, DeviceMcpError
from .media import OpusPacketPacer, StreamingPcmToOpus
from .providers import (
    RealtimeAsrSession,
    RealtimeProviderBundle,
    RealtimeTtsSession,
    TranscriptionResult,
)
from .reply_policy import build_voice_reply_policy
from .tools import ToolRegistry, create_search_provider

logger = logging.getLogger(__name__)
MAX_UTTERANCE_BYTES = 1024 * 1024
MAX_CONSECUTIVE_NOISE_RETRIES = 1


_SPOKEN_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_SPOKEN_INTERNAL_TAG_RE = re.compile(
    r"\[(?:mood|emotion|state|tool|system)\s*:[^\]]*\]", re.IGNORECASE
)
_SPOKEN_FACE_CONTROL_RE = re.compile(r"\[\[face:[^\]\r\n]*(?:\]\]|\])?", re.IGNORECASE)
_SPOKEN_STAGE_DIRECTION_RE = re.compile(
    r"[（(][^）)]{0,24}(?:点头|微笑|叹气|沉默|转身|看着|轻轻|笑)[^）)]{0,24}[）)]"
)
_LEADING_WAKE_NAME_RE = re.compile(r"^(?:你好小灿|小灿)[，,\s]*")
_NON_SPEECH_FILLER_STRIP_RE = re.compile(r"[\s，。！？!?、…,.~～]+")
_NON_SPEECH_FILLER_CHARS = frozenset("嗯啊呃额唔哼哦")


def sanitize_spoken_text(text: str) -> str:
    """Return text suitable for TTS without leaking visual or internal markup."""
    text = _SPOKEN_URL_RE.sub("", text)
    text = _SPOKEN_FACE_CONTROL_RE.sub("", text)
    text = _SPOKEN_INTERNAL_TAG_RE.sub("", text)
    text = _SPOKEN_STAGE_DIRECTION_RE.sub("", text)
    text = re.sub(r"[*_#>`~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" ，,：:")


def is_non_speech_filler(text: str) -> bool:
    """Identify short ASR filler hallucinations that must not start an AI turn."""
    normalized = _NON_SPEECH_FILLER_STRIP_RE.sub("", text)
    return bool(normalized) and len(normalized) <= 6 and all(
        character in _NON_SPEECH_FILLER_CHARS for character in normalized
    )


@dataclass
class _NoiseTurnBudget:
    consecutive_discards: int = 0

    def consume_retry(self) -> bool:
        self.consecutive_discards += 1
        return self.consecutive_discards <= MAX_CONSECUTIVE_NOISE_RETRIES

    def reset(self) -> None:
        self.consecutive_discards = 0


@dataclass(frozen=True)
class AgentSnapshot:
    agent_id: str
    usage_profile_id: str
    usage_profile_kind: str
    config_version: int
    system_prompt: str
    memory_consent: bool
    memories: list[str]
    asr_provider: str
    asr_model: str
    llm_provider: str
    llm_model: str
    tts_provider: str
    tts_model: str
    voice: str
    llm_temperature: float
    tts_speech_rate: float
    tools: dict[str, bool]
    asr_cost_micros_per_minute: int
    llm_input_cost_micros_per_million_tokens: int
    llm_output_cost_micros_per_million_tokens: int
    tts_cost_micros_per_10k_chars: int


class _UnavailableRealtimeAsrSession:
    """Keep the device turn alive until bounded batch ASR can use its buffered audio."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def send_audio(self, frame: bytes) -> None:
        del frame

    def endpoint_detected(self) -> bool:
        return False

    async def finish(self) -> TranscriptionResult:
        raise self._error

    async def cancel(self) -> None:
        return None


class SentenceBuffer:
    def __init__(
        self,
        max_chars: int = 32,
        min_clause_chars: int = 16,
        first_chunk_chars: int = 16,
    ) -> None:
        self._text = ""
        self._max_chars = max_chars
        self._min_clause_chars = min_clause_chars
        self._first_chunk_chars = first_chunk_chars
        self._first_chunk_sent = False

    def feed(self, text: str) -> list[str]:
        self._text += text
        sentences: list[str] = []
        while self._text:
            boundary = next(
                (index + 1 for index, char in enumerate(self._text) if char in "。！？!?；;\n"),
                None,
            )
            if boundary is None:
                clause_boundaries = [
                    index + 1
                    for index, char in enumerate(self._text[: self._max_chars])
                    if char in "，,：:、" and index + 1 >= self._min_clause_chars
                ]
                if clause_boundaries:
                    boundary = clause_boundaries[-1]
                elif not self._first_chunk_sent and len(self._text) >= self._first_chunk_chars:
                    boundary = self._first_chunk_chars
                elif len(self._text) >= self._max_chars:
                    boundary = self._max_chars
                else:
                    break
            sentence = self._text[:boundary].strip()
            self._text = self._text[boundary:]
            if sentence:
                sentences.append(sentence)
                self._first_chunk_sent = True
        return sentences

    def flush(self) -> str | None:
        sentence = self._text.strip()
        self._text = ""
        return sentence or None


class PlaybackHandshake:
    def __init__(self, *, drain_timeout_seconds: float = 1.0) -> None:
        self.reply_id: str | None = None
        self.turn_id: str | None = None
        self.ready = asyncio.Event()
        self.drained = asyncio.Event()
        self._drain_timeout_seconds = drain_timeout_seconds
        self.strict_ack = False

    def configure(self, *, strict_ack: bool) -> None:
        self.strict_ack = strict_ack

    def begin(self, turn_id: str) -> str:
        self.reply_id = str(uuid.uuid4())
        self.turn_id = turn_id
        self.ready = asyncio.Event()
        self.drained = asyncio.Event()
        return self.reply_id

    def acknowledge(self, state: str, reply_id: str, turn_id: str = "") -> bool:
        if not reply_id or reply_id != self.reply_id:
            return False
        if turn_id and self.turn_id and turn_id != self.turn_id:
            return False
        if state == "ready":
            self.ready.set()
            return True
        if state == "drained":
            self.drained.set()
            return True
        return False

    async def wait_ready(self, reply_id: str) -> bool:
        if reply_id != self.reply_id:
            return False
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=2.0)
            return True
        except TimeoutError:
            return False

    async def wait_drained(self, reply_id: str) -> bool:
        if reply_id != self.reply_id:
            return False
        try:
            await asyncio.wait_for(self.drained.wait(), timeout=self._drain_timeout_seconds)
            return True
        except TimeoutError:
            return False

    def clear(self, reply_id: str | None = None) -> None:
        if reply_id is None or reply_id == self.reply_id:
            self.reply_id = None
            self.turn_id = None
            self.ready.set()
            self.drained.set()


async def _start_playback(
    websocket: WebSocket,
    lease: ConnectionLease,
    playback: PlaybackHandshake,
    turn_id: str,
) -> str:
    reply_id = playback.begin(turn_id)
    delivered = await websocket.app.state.device_connections.send_json_for_lease(
        lease,
        {"type": "tts", "state": "start", "turn_id": turn_id, "reply_id": reply_id},
    )
    if not delivered:
        playback.clear(reply_id)
        raise ConnectionError("device connection lease expired before TTS start")
    if not await playback.wait_ready(reply_id):
        if playback.strict_ack:
            playback.clear(reply_id)
            raise TimeoutError("tts-ready-timeout")
        logger.warning(
            "legacy device %s did not acknowledge TTS ready; using paced compatibility mode",
            lease.serial_number,
        )
    return reply_id


async def _stop_playback(
    websocket: WebSocket,
    lease: ConnectionLease,
    playback: PlaybackHandshake,
    reply_id: str,
    turn_id: str,
    *,
    wait_for_drain: bool,
) -> bool:
    delivered = await websocket.app.state.device_connections.send_json_for_lease(
        lease,
        {"type": "tts", "state": "stop", "turn_id": turn_id, "reply_id": reply_id},
    )
    if not delivered:
        playback.clear(reply_id)
        return False
    drained = not wait_for_drain or await playback.wait_drained(reply_id)
    if not drained:
        logger.warning("device %s did not acknowledge TTS drained", lease.serial_number)
    playback.clear(reply_id)
    return drained or not playback.strict_ack


async def _load_snapshot(session: AsyncSession, device: Device, settings) -> AgentSnapshot:
    if device.active_agent_id is None:
        user = await session.get(User, device.owner_user_id)
        if user is None:
            raise RuntimeError("device owner is missing")
        agent = await ensure_default_agent(session, user)
        device.active_agent_id = agent.id
    else:
        agent = await session.get(Agent, device.active_agent_id)
    if agent is None:
        raise RuntimeError("active agent is missing")
    profile = await session.get(UsageProfile, device.active_profile_id or agent.usage_profile_id)
    if profile is None or profile.owner_user_id != device.owner_user_id:
        raise RuntimeError("active usage profile is missing")
    if agent.usage_profile_id != profile.id:
        replacement = await session.scalar(
            select(Agent).where(Agent.usage_profile_id == profile.id).order_by(Agent.created_at)
        )
        if replacement is None:
            raise RuntimeError("active usage profile has no assistant")
        agent = replacement
        device.active_agent_id = agent.id
    device.active_profile_id = profile.id
    model = await session.get(ModelPreset, agent.model_preset_id)
    voice = await session.get(VoicePreset, agent.voice_preset_id)
    if model is None or not model.enabled or voice is None or not voice.enabled:
        raise RuntimeError("agent preset is unavailable")
    memories: list[str] = []
    profile_memory_allowed = profile.kind == UsageProfileKind.ADULT.value or profile.memory_consent
    if agent.memory_consent and profile_memory_allowed:
        encrypted = list(
            await session.scalars(
                select(AgentMemory.encrypted_value).where(AgentMemory.agent_id == agent.id)
            )
        )
        memories = [decrypt_memory(value, settings) for value in encrypted]
    return AgentSnapshot(
        agent_id=agent.id,
        usage_profile_id=profile.id,
        usage_profile_kind=profile.kind,
        config_version=agent.config_version,
        system_prompt=agent.system_prompt,
        memory_consent=agent.memory_consent and profile_memory_allowed,
        memories=memories,
        asr_provider=model.asr_provider,
        asr_model=model.asr_model,
        llm_provider=model.llm_provider,
        llm_model=model.llm_model,
        tts_provider=model.tts_provider,
        tts_model=model.tts_model,
        voice=voice.voice,
        llm_temperature=agent.llm_temperature,
        tts_speech_rate=agent.tts_speech_rate,
        tools=json.loads(agent.tools_json or "{}"),
        asr_cost_micros_per_minute=model.asr_cost_micros_per_minute,
        llm_input_cost_micros_per_million_tokens=(model.llm_input_cost_micros_per_million_tokens),
        llm_output_cost_micros_per_million_tokens=(model.llm_output_cost_micros_per_million_tokens),
        tts_cost_micros_per_10k_chars=model.tts_cost_micros_per_10k_chars,
    )


async def _send_error(
    websocket: WebSocket, lease: ConnectionLease, code: str, message: str
) -> None:
    delivered = await websocket.app.state.device_connections.send_json_for_lease(
        lease, {"type": "error", "code": code, "message": message}
    )
    if not delivered:
        logger.info(
            "device %s disconnected before error %s was delivered",
            lease.serial_number,
            code,
        )


async def _send_turn_error_and_reset(
    websocket: WebSocket,
    lease: ConnectionLease,
    playback: PlaybackHandshake,
    turn_id: str,
    code: str,
    message: str,
) -> None:
    """End a failed turn through the same handshake as a zero-audio reply."""
    await _send_error(websocket, lease, code, message)
    reply_id = await _start_playback(websocket, lease, playback, turn_id)
    await _stop_playback(
        websocket,
        lease,
        playback,
        reply_id,
        turn_id,
        wait_for_drain=True,
    )


async def _receive_device_message(
    websocket: WebSocket,
    *,
    timeout_seconds: float,
    stop_event: asyncio.Event | None = None,
) -> dict[str, object] | None:
    if stop_event is None:
        try:
            return await asyncio.wait_for(websocket.receive(), timeout=timeout_seconds)
        except TimeoutError:
            return None

    receive_task = asyncio.create_task(websocket.receive())
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait(
            {receive_task, stop_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done or stop_task in done:
            return None
        return receive_task.result()
    finally:
        for task in (receive_task, stop_task):
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _record_device_heartbeat(
    session_factory: async_sessionmaker[AsyncSession],
    device_session_id: str,
) -> None:
    async with session_factory() as session:
        device_session = await session.get(DeviceSession, device_session_id)
        if device_session is None:
            return
        device_session.heartbeat_at = datetime.now(UTC)
        await session.commit()


async def _record_turn(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: str,
    user_id: str,
    device_id: str,
    snapshot: AgentSnapshot,
    audio_duration_ms: int,
    transcript: str,
    reply: str,
    asr_latency_ms: int,
    llm_latency_ms: int,
    tts_latency_ms: int,
    first_audio_latency_ms: int | None,
    safety_category: str | None,
    fallback_operations: set[str],
) -> None:
    estimated_input_tokens = max(1, len(transcript) // 4)
    estimated_output_tokens = max(1, len(reply) // 4)
    asr_cost = round(snapshot.asr_cost_micros_per_minute * audio_duration_ms / 60_000)
    llm_cost = round(
        snapshot.llm_input_cost_micros_per_million_tokens * estimated_input_tokens / 1_000_000
        + snapshot.llm_output_cost_micros_per_million_tokens * estimated_output_tokens / 1_000_000
    )
    tts_cost = round(snapshot.tts_cost_micros_per_10k_chars * len(reply) / 10_000)
    total_cost = asr_cost + llm_cost + tts_cost
    async with session_factory() as session:
        conversation = await session.get(ConversationSession, conversation_id)
        if conversation is None:
            return
        conversation.turn_count += 1
        conversation.provider_cost_micros += total_cost
        if conversation.first_audio_latency_ms is None and first_audio_latency_ms is not None:
            conversation.first_audio_latency_ms = first_audio_latency_ms
        session.add(
            UsageEvent(
                user_id=user_id,
                device_id=device_id,
                kind="voice-turn",
                quantity=1,
                provider_cost_micros=total_cost,
            )
        )
        session.add_all(
            [
                ProviderUsage(
                    session_id=conversation_id,
                    user_id=user_id,
                    device_id=device_id,
                    provider=snapshot.asr_provider,
                    model=snapshot.asr_model,
                    operation="asr",
                    input_units=audio_duration_ms,
                    output_units=len(transcript),
                    latency_ms=asr_latency_ms,
                    cost_micros=asr_cost,
                    error_code=("fallback-batch" if "asr" in fallback_operations else None),
                ),
                ProviderUsage(
                    session_id=conversation_id,
                    user_id=user_id,
                    device_id=device_id,
                    provider=snapshot.llm_provider,
                    model=snapshot.llm_model,
                    operation="llm",
                    input_units=estimated_input_tokens,
                    output_units=estimated_output_tokens,
                    latency_ms=llm_latency_ms,
                    cost_micros=llm_cost,
                    error_code=("fallback-batch" if "llm" in fallback_operations else None),
                ),
                ProviderUsage(
                    session_id=conversation_id,
                    user_id=user_id,
                    device_id=device_id,
                    provider=snapshot.tts_provider,
                    model=snapshot.tts_model,
                    operation="tts",
                    input_units=len(reply),
                    latency_ms=tts_latency_ms,
                    cost_micros=tts_cost,
                    error_code=("fallback-batch" if "tts" in fallback_operations else None),
                ),
            ]
        )
        add_audit_event(
            session,
            actor_type="device",
            actor_id=device_id,
            action="voice.turn-completed",
            payload={
                "safety_category": safety_category,
                "agent_id": snapshot.agent_id,
                "config_version": snapshot.config_version,
                "fallback_operations": sorted(fallback_operations),
            },
        )
        await session.commit()


async def _speak_sentence(
    websocket: WebSocket,
    lease: ConnectionLease,
    sentence: str,
    tts: RealtimeTtsSession,
    encoder: StreamingPcmToOpus | None,
    direct_pacer: OpusPacketPacer,
    turn_id: str,
) -> None:
    await websocket.app.state.device_connections.send_json_for_lease(
        lease,
        {"type": "tts", "state": "sentence_start", "turn_id": turn_id, "text": sentence},
    )
    async for audio in tts.synthesize(sentence):
        if encoder is None:
            if not await direct_pacer.send(audio):
                return
        else:
            await encoder.write(audio)


async def _speak_fixed_message(
    websocket: WebSocket,
    lease: ConnectionLease,
    voice: str,
    speech_rate: float,
    message: str,
    playback: PlaybackHandshake,
) -> bool:
    """Speak a product-owned policy message without invoking the LLM."""
    serial = lease.serial_number
    providers: RealtimeProviderBundle = websocket.app.state.realtime_providers
    fallback: ProviderBundle | None = websocket.app.state.fallback_providers
    tts: RealtimeTtsSession | None = None
    encoder: StreamingPcmToOpus | None = None
    packet_task: asyncio.Task[None] | None = None
    reply_id: str | None = None
    turn_id = str(uuid.uuid4())
    interrupted = False
    pacer = OpusPacketPacer(
        lambda packet: websocket.app.state.device_connections.send_bytes_for_lease(
            lease, packet
        ),
        startup_burst_packets=5,
    )

    async def send_packets() -> None:
        assert encoder is not None
        async for packet in encoder.packets(prebuffer_packets=5):
            if not await pacer.send(packet):
                return

    try:
        try:
            tts = await providers.open_tts(voice, speech_rate)
        except Exception:
            if fallback is None:
                raise
            logger.warning("realtime TTS failed for policy prompt on %s; using fallback", serial)
            reply_id = await _start_playback(websocket, lease, playback, turn_id)
            await websocket.app.state.device_connections.send_json_for_lease(
                lease,
                {"type": "tts", "state": "sentence_start", "turn_id": turn_id, "text": message},
            )
            for packet in await fallback.speech.synthesize(message):
                if not await pacer.send(packet):
                    break
            return False

        reply_id = await _start_playback(websocket, lease, playback, turn_id)
        if not providers.mock:
            encoder = StreamingPcmToOpus(websocket.app.state.settings.ffmpeg_path)
            await encoder.start()
            packet_task = asyncio.create_task(send_packets())
        await _speak_sentence(websocket, lease, message, tts, encoder, pacer, turn_id)
        await tts.finish()
        if encoder is not None:
            await encoder.finish()
        if packet_task is not None:
            await packet_task
    except asyncio.CancelledError:
        interrupted = True
        raise
    except Exception:
        logger.exception("fixed policy prompt failed for device %s", serial)
    finally:
        if tts is not None:
            with contextlib.suppress(Exception):
                await tts.cancel()
        if encoder is not None and packet_task is not None and not packet_task.done():
            with contextlib.suppress(Exception):
                await encoder.cancel()
            packet_task.cancel()
        if reply_id is not None:
            await _stop_playback(
                websocket,
                lease,
                playback,
                reply_id,
                turn_id,
                wait_for_drain=not interrupted,
            )
    return False


async def _process_turn(
    websocket: WebSocket,
    lease: ConnectionLease,
    device_id: str,
    user_id: str,
    conversation_id: str,
    turn_id: str,
    snapshot: AgentSnapshot,
    asr: RealtimeAsrSession,
    audio_frames: list[bytes],
    audio_duration_ms: int,
    history: list[dict[str, str]],
    turn_started: float,
    playback: PlaybackHandshake,
    noise_turn_budget: _NoiseTurnBudget,
    user_exit_event: asyncio.Event,
    telemetry_tasks: set[asyncio.Task[None]],
    mcp_client: DeviceMcpClient | None = None,
    strip_wake_name: bool = False,
) -> bool:
    serial = lease.serial_number
    providers: RealtimeProviderBundle = websocket.app.state.realtime_providers
    fallback: ProviderBundle | None = websocket.app.state.fallback_providers
    router = EmotionRouter()
    provider_settings = getattr(providers, "settings", websocket.app.state.settings)
    search_provider = create_search_provider(provider_settings)
    tool_registry = ToolRegistry(search_provider=search_provider)
    enabled_tools = {name: enabled for name, enabled in snapshot.tools.items() if enabled}
    tool_schemas = tool_registry.definitions(enabled_tools)
    if mcp_client is not None:
        tool_schemas.extend(mcp_client.openai_tools(enabled_tools))

    async def execute_tool(name: str, arguments: dict[str, object]) -> str:
        if mcp_client is not None and mcp_client.can_call(name):
            return await mcp_client.call(name, arguments)
        return await tool_registry.execute(name, arguments)

    tts: RealtimeTtsSession | None = None
    encoder: StreamingPcmToOpus | None = None
    packet_task: asyncio.Task[None] | None = None
    tts_started = False
    batch_tts = False
    first_audio_latency_ms: int | None = None
    fallback_operations: set[str] = set()
    reply_id: str | None = None
    interrupted = False
    playback_stopped = False
    pacer = OpusPacketPacer(
        lambda packet: websocket.app.state.device_connections.send_bytes_for_lease(
            lease, packet
        ),
        startup_burst_packets=5,
    )

    async def send_packets() -> None:
        nonlocal first_audio_latency_ms
        assert encoder is not None
        async for packet in encoder.packets(prebuffer_packets=5):
            if first_audio_latency_ms is None:
                first_audio_latency_ms = int((time.perf_counter() - turn_started) * 1000)
            if not await pacer.send(packet):
                return

    try:
        asr_started = time.perf_counter()
        try:
            transcription = await asr.finish()
        except Exception as exc:
            with contextlib.suppress(Exception):
                await asr.cancel()
            if fallback is None:
                await _send_turn_error_and_reset(
                    websocket,
                    lease,
                    playback,
                    turn_id,
                    "asr-realtime-invalid",
                    "realtime speech recognition is unavailable",
                )
                return False
            error_code = getattr(exc, "code", "unknown")
            logger.warning(
                "realtime ASR failed for %s with %s; using batch fallback",
                serial,
                error_code,
            )
            fallback_operations.add("asr")
            try:
                transcript = await fallback.speech.transcribe(audio_frames)
            except Exception as fallback_exc:
                fallback_error_code = getattr(fallback_exc, "code", "unknown")
                logger.warning(
                    "batch ASR fallback failed for %s with %s",
                    serial,
                    fallback_error_code,
                )
                await _send_turn_error_and_reset(
                    websocket,
                    lease,
                    playback,
                    turn_id,
                    "asr-fallback-failed",
                    "speech recognition is temporarily unavailable",
                )
                return False
            detected_emotion = getattr(fallback.speech, "last_emotion", None) or "neutral"
            transcription = TranscriptionResult(text=transcript, emotion=detected_emotion)
        asr_latency_ms = int((time.perf_counter() - asr_started) * 1000)
        transcript = transcription.text.strip()
        if strip_wake_name:
            transcript = _LEADING_WAKE_NAME_RE.sub("", transcript, count=1).strip()
        if not transcript or is_non_speech_filler(transcript):
            # Realtime ASR can hallucinate a one-character filler from room
            # noise during the follow-up window. This is expected control flow,
            # not a device error: an error message drives the display into its
            # alert face. Never reject a meaningful transcript solely because
            # its audio is shorter than one second (for example "好" or "几点").
            logger.info("discarded ASR non-speech serial=%s code=asr-no-speech", serial)
            if noise_turn_budget.consume_retry():
                await websocket.app.state.device_connections.send_json_for_lease(
                    lease,
                    {"type": "listen", "state": "resume", "turn_id": turn_id},
                )
            else:
                await websocket.app.state.device_connections.send_json_for_lease(
                    lease,
                    {"type": "listen", "state": "standby", "turn_id": turn_id},
                )
            return False
        noise_turn_budget.reset()
        await websocket.app.state.device_connections.send_json_for_lease(
            lease,
            {
                "type": "stt",
                "text": transcript,
                "emotion": transcription.emotion,
                "turn_id": turn_id,
            },
        )

        async with websocket.app.state.session_factory() as session:
            quota = await quota_for_user(session, user_id, websocket.app.state.settings)
        if quota.remaining <= 0:
            await _send_turn_error_and_reset(
                websocket,
                lease,
                playback,
                turn_id,
                "quota-exhausted",
                "monthly voice quota exhausted",
            )
            return False

        safety = evaluate_text(transcript)
        if safety.end_session:
            # "小灿闭嘴" is a control command, not another assistant reply.
            # Tell the device to enter standby before closing the transport so
            # it does not interpret a normal exit as a failed audio channel.
            await websocket.app.state.device_connections.send_json_for_lease(
                lease,
                {
                    "type": "listen",
                    "state": "standby",
                    "reason": "user-exit",
                    "turn_id": turn_id,
                },
            )
            user_exit_event.set()
            return True

        emotion = router.route(transcription.emotion, safety.category)
        await websocket.app.state.device_connections.send_json_for_lease(
            lease, {"type": "llm", "emotion": emotion.thinking_emotion, "turn_id": turn_id}
        )

        sentence_buffer = SentenceBuffer()
        face_parser = FaceControlParser()
        reply_policy = build_voice_reply_policy(transcript)
        voice_system_prompt = (
            snapshot.system_prompt.rstrip()
            + "\n\n"
            + reply_policy.context
            + "\n不要输出 Markdown、网址或舞台动作。回复正文前必须先输出且只输出一个"
            "表情控制标记，格式为 [[face:emotion]]，emotion 只能是 "
            + "/".join(sorted(SUPPORTED_FACE_EMOTIONS))
            + "。如果第二句情绪明显变化，可以在第一句完整结束后再输出一个标记；"
            "整次回复最多两个标记，标记之外不要输出其他内部标签。"
        )
        reply_parts: list[str] = []
        spoken_parts: list[str] = []
        spoken_chars = 0
        spoken_segments = 0
        llm_started = time.perf_counter()
        first_sentence_at: float | None = None
        tts_started_at: float | None = None
        pending_reply_emotion = emotion.reply_emotion
        sent_reply_emotion: str | None = None

        def apply_face_event(event: FaceControlEvent) -> None:
            nonlocal pending_reply_emotion
            if event.kind == "emotion" and event.value in SUPPORTED_FACE_EMOTIONS:
                pending_reply_emotion = event.value

        async def speak(sentence: str) -> None:
            nonlocal encoder, first_sentence_at, packet_task, tts, tts_started
            nonlocal batch_tts, first_audio_latency_ms, tts_started_at, reply_id
            nonlocal spoken_chars, spoken_segments
            nonlocal sent_reply_emotion
            sentence = sanitize_spoken_text(sentence)
            if not sentence or spoken_segments >= reply_policy.max_spoken_segments:
                return
            sentence = sentence[: reply_policy.max_spoken_chars - spoken_chars].strip()
            if not sentence:
                return
            if pending_reply_emotion != sent_reply_emotion:
                await websocket.app.state.device_connections.send_json_for_lease(
                    lease,
                    {
                        "type": "llm",
                        "emotion": pending_reply_emotion,
                        "turn_id": turn_id,
                    },
                )
                sent_reply_emotion = pending_reply_emotion
            if tts is None and not batch_tts:
                try:
                    tts = await providers.open_tts(snapshot.voice, snapshot.tts_speech_rate)
                except Exception:
                    if fallback is None:
                        raise
                    logger.warning("realtime TTS failed for %s; using batch fallback", serial)
                    fallback_operations.add("tts")
                    batch_tts = True
                reply_id = await _start_playback(websocket, lease, playback, turn_id)
                if tts is not None and not providers.mock:
                    encoder = StreamingPcmToOpus(websocket.app.state.settings.ffmpeg_path)
                    await encoder.start()
                    packet_task = asyncio.create_task(send_packets())
                tts_started = True
                tts_started_at = time.perf_counter()
            if first_sentence_at is None:
                first_sentence_at = time.perf_counter()
            spoken_parts.append(sentence)
            spoken_chars += len(sentence)
            spoken_segments += 1
            if batch_tts:
                assert fallback is not None
                await websocket.app.state.device_connections.send_json_for_lease(
                    lease,
                    {
                        "type": "tts",
                        "state": "sentence_start",
                        "turn_id": turn_id,
                        "text": sentence,
                    },
                )
                for packet in await fallback.speech.synthesize(sentence):
                    if first_audio_latency_ms is None:
                        first_audio_latency_ms = int((time.perf_counter() - turn_started) * 1000)
                    if not await pacer.send(packet):
                        return
            else:
                assert tts is not None
                await _speak_sentence(websocket, lease, sentence, tts, encoder, pacer, turn_id)

        if safety.fixed_response:
            reply_parts.append(safety.fixed_response)
            await speak(safety.fixed_response)
        else:
            try:
                llm_kwargs: dict[str, object] = {}
                if tool_schemas:
                    llm_kwargs = {"tools": tool_schemas, "tool_executor": execute_tool}
                async for token in providers.llm.reply_stream(
                    transcript,
                    history,
                    snapshot.memories,
                    system_prompt=voice_system_prompt,
                    model=snapshot.llm_model,
                    temperature=snapshot.llm_temperature,
                    **llm_kwargs,
                ):
                    for face_event in face_parser.feed(token):
                        apply_face_event(face_event)
                        if face_event.kind != "text":
                            continue
                        reply_parts.append(face_event.value)
                        for sentence in sentence_buffer.feed(face_event.value):
                            output_safety = evaluate_text(sentence)
                            if (
                                output_safety.fixed_response
                                and output_safety.category != "user-exit"
                            ):
                                sentence = output_safety.fixed_response
                            await speak(sentence)
                for face_event in face_parser.flush():
                    apply_face_event(face_event)
                    if face_event.kind != "text":
                        continue
                    reply_parts.append(face_event.value)
                    for sentence in sentence_buffer.feed(face_event.value):
                        output_safety = evaluate_text(sentence)
                        if output_safety.fixed_response and output_safety.category != "user-exit":
                            sentence = output_safety.fixed_response
                        await speak(sentence)
            except Exception:
                if fallback is None or spoken_parts:
                    raise
                logger.warning("streaming LLM failed for %s; using batch fallback", serial)
                fallback_operations.add("llm")
                reply_parts.clear()
                sentence_buffer = SentenceBuffer()
                fallback_text = await fallback.llm.reply(
                    transcript,
                    snapshot.memories,
                    history=history,
                    system_prompt=voice_system_prompt,
                )
                reply_parts.append(fallback_text)
                for sentence in sentence_buffer.feed(fallback_text):
                    await speak(sentence)
            trailing = sentence_buffer.flush()
            if trailing:
                await speak(trailing)

        if not tts_started or not reply_parts:
            await _send_turn_error_and_reset(
                websocket,
                lease,
                playback,
                turn_id,
                "empty-reply",
                "AI returned an empty response",
            )
            return False

        if tts is not None:
            await tts.finish()
        if encoder is not None:
            await encoder.finish()
        if packet_task is not None:
            await packet_task
        if providers.mock and first_audio_latency_ms is None:
            first_audio_latency_ms = int((time.perf_counter() - turn_started) * 1000)

        assert reply_id is not None
        drained = await _stop_playback(
            websocket,
            lease,
            playback,
            reply_id,
            turn_id,
            wait_for_drain=True,
        )
        playback_stopped = True
        if not drained:
            await _send_error(
                websocket,
                lease,
                "tts-drained-timeout",
                "audio playback did not finish cleanly",
            )
            return False

        reply = "".join(spoken_parts).strip()
        llm_latency_ms = int(((first_sentence_at or time.perf_counter()) - llm_started) * 1000)
        tts_latency_ms = int((time.perf_counter() - (tts_started_at or llm_started)) * 1000)
        history.extend(
            [
                {"role": "user", "content": transcript},
                {"role": "assistant", "content": reply},
            ]
        )
        del history[:-20]
        telemetry_task = asyncio.create_task(
            _record_turn(
                websocket.app.state.session_factory,
                conversation_id=conversation_id,
                user_id=user_id,
                device_id=device_id,
                snapshot=snapshot,
                audio_duration_ms=audio_duration_ms,
                transcript=transcript,
                reply=reply,
                asr_latency_ms=asr_latency_ms,
                llm_latency_ms=llm_latency_ms,
                tts_latency_ms=tts_latency_ms,
                first_audio_latency_ms=first_audio_latency_ms,
                safety_category=safety.category,
                fallback_operations=fallback_operations,
            )
        )
        telemetry_tasks.add(telemetry_task)

        def observe_telemetry(task: asyncio.Task[None]) -> None:
            telemetry_tasks.discard(task)
            try:
                task.result()
            except Exception:
                logger.exception("turn telemetry write failed for device %s", serial)

        telemetry_task.add_done_callback(observe_telemetry)
        return safety.end_session
    except asyncio.CancelledError:
        interrupted = True
        await asr.cancel()
        if tts is not None:
            await tts.cancel()
        if encoder is not None:
            await encoder.cancel()
        if packet_task is not None:
            packet_task.cancel()
        raise
    except TimeoutError as exc:
        code = str(exc) or "tts-ready-timeout"
        logger.warning("voice turn timed out for device %s code=%s", serial, code)
        await _send_error(websocket, lease, code, "audio playback handshake timed out")
        return False
    except Exception:
        logger.exception("voice turn failed for device %s", serial)
        if tts_started:
            await _send_error(websocket, lease, "ai-unavailable", "AI response unavailable")
        else:
            await _send_turn_error_and_reset(
                websocket,
                lease,
                playback,
                turn_id,
                "ai-unavailable",
                "AI response unavailable",
            )
        return False
    finally:
        if tts_started and reply_id is not None and not playback_stopped:
            await _stop_playback(
                websocket,
                lease,
                playback,
                reply_id,
                turn_id,
                wait_for_drain=not interrupted,
            )


async def _save_session_summary(
    websocket: WebSocket,
    conversation_id: str,
    user_id: str,
    snapshot: AgentSnapshot,
    history: list[dict[str, str]],
) -> None:
    if not snapshot.memory_consent or not history:
        return
    prompt = "请把这次对话概括为不超过120字的偏好和待办摘要，不要记录敏感原文。"
    parts: list[str] = []
    try:
        async for token in websocket.app.state.realtime_providers.llm.reply_stream(
            prompt,
            history[-20:],
            [],
            system_prompt="只输出简短、客观的会话摘要。",
            model=snapshot.llm_model,
            temperature=0.2,
        ):
            parts.append(token)
        summary = "".join(parts).strip()[:500]
        if not summary:
            return
        async with websocket.app.state.session_factory() as session:
            session.add(
                EncryptedSessionSummary(
                    session_id=conversation_id,
                    user_id=user_id,
                    agent_id=snapshot.agent_id,
                    encrypted_summary=encrypt_memory(summary, websocket.app.state.settings),
                )
            )
            await session.commit()
    except Exception:
        logger.exception("session summary failed for conversation %s", conversation_id)


async def _handle_device_config_ack(
    session_factory: async_sessionmaker[AsyncSession],
    device_id: str,
    message: dict[str, object],
) -> None:
    command_id = message.get("command_id")
    config_version = message.get("config_version")
    ack_status = message.get("status")
    applied = message.get("applied")
    if (
        not isinstance(command_id, str)
        or not isinstance(config_version, int)
        or isinstance(config_version, bool)
        or ack_status not in {"applied", "failed"}
    ):
        logger.warning("ignored malformed device configuration acknowledgement")
        return

    async with session_factory() as session:
        command = await session.get(DeviceCommand, command_id)
        configuration = await session.get(DeviceConfiguration, device_id)
        if (
            command is None
            or command.device_id != device_id
            or command.command_type != "device-config"
            or configuration is None
        ):
            logger.warning("ignored unknown device configuration acknowledgement %s", command_id)
            return
        try:
            expected_version = int(json.loads(command.payload_json)["config_version"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "invalid-command-payload"
            await session.commit()
            return
        if config_version != expected_version:
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "ack-version-mismatch"
            await session.commit()
            return

        now = datetime.now(UTC)
        if ack_status == "failed":
            error_code = message.get("error_code")
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = (
                error_code[:80] if isinstance(error_code, str) and error_code else "device-rejected"
            )
            if config_version == configuration.desired_version:
                configuration.last_error_code = command.error_code
            await session.commit()
            return

        if not isinstance(applied, dict):
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "invalid-ack-payload"
            if config_version == configuration.desired_version:
                configuration.last_error_code = command.error_code
            await session.commit()
            return
        speaker_volume = applied.get("speaker_volume")
        screen_brightness = applied.get("screen_brightness")
        if (
            not isinstance(speaker_volume, int)
            or isinstance(speaker_volume, bool)
            or not 10 <= speaker_volume <= 100
            or not isinstance(screen_brightness, int)
            or isinstance(screen_brightness, bool)
            or not 10 <= screen_brightness <= 100
        ):
            command.status = DeviceCommandStatus.FAILED.value
            command.error_code = "invalid-ack-values"
            if config_version == configuration.desired_version:
                configuration.last_error_code = command.error_code
            await session.commit()
            return

        command.status = DeviceCommandStatus.APPLIED.value
        command.applied_at = now
        command.error_code = None
        if config_version >= configuration.applied_version:
            configuration.applied_version = config_version
            configuration.applied_speaker_volume = speaker_volume
            configuration.applied_screen_brightness = screen_brightness
            configuration.applied_at = now
        if config_version == configuration.desired_version:
            configuration.last_error_code = None
        add_audit_event(
            session,
            actor_type="device",
            actor_id=device_id,
            action="device.configuration-applied",
            payload={"command_id": command.id, "config_version": config_version},
        )
        await session.commit()


async def serve_device_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    serial = websocket.headers.get("device-id", "")
    authorization = websocket.headers.get("authorization", "")
    secret = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    client = getattr(websocket, "client", None)
    logger.info(
        "device websocket connect attempt serial=%s client=%s has_auth=%s",
        serial,
        client,
        bool(secret),
    )

    async with session_factory() as session:
        device = await session.scalar(select(Device).where(Device.serial_number == serial))
        valid_secret = device is not None and verify_secret(
            secret, device.credential_hash, settings.device_credential_pepper
        )
        valid_token = verify_device_session_token(secret, serial, settings)
        if device is None or not (valid_secret or valid_token):
            logger.warning(
                "device websocket rejected serial=%s device_found=%s "
                "valid_secret=%s valid_token=%s",
                serial,
                device is not None,
                valid_secret,
                valid_token,
            )
            await websocket.close(code=4401, reason="invalid device credential")
            return
        if device.lifecycle != DeviceLifecycle.OWNED.value or not device.owner_user_id:
            logger.warning(
                "device websocket rejected inactive serial=%s lifecycle=%s owner=%s",
                serial,
                device.lifecycle,
                bool(device.owner_user_id),
            )
            await websocket.close(code=4403, reason="device is not active and owned")
            return
        snapshot = await _load_snapshot(session, device, settings)
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
            gateway_id=settings.gateway_id,
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
    logger.info(
        "device websocket accepted serial=%s connection_id=%s auth=%s",
        serial,
        connection_id,
        "token" if valid_token else "secret",
    )
    connection_lease = await websocket.app.state.device_connections.connect(
        serial, websocket, connection_id
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
    heartbeat_timed_out = False
    continuous_reminder_sent = False
    playback = PlaybackHandshake()
    noise_turn_budget = _NoiseTurnBudget()
    user_exit_event = asyncio.Event()
    telemetry_tasks: set[asyncio.Task[None]] = set()
    mcp_client: DeviceMcpClient | None = None
    mcp_initialize_task: asyncio.Task[None] | None = None
    # A warm device WebSocket can span multiple sleep/wake sessions. Only a
    # real local wake arms prefix removal for the immediately following turn.
    first_turn_pending = False

    def asr_endpoint_detected(asr: RealtimeAsrSession) -> bool:
        detector = getattr(asr, "endpoint_detected", None)
        return bool(detector and detector())

    async def open_asr_for_turn() -> RealtimeAsrSession:
        try:
            return await websocket.app.state.realtime_providers.open_asr()
        except Exception as exc:
            logger.warning(
                "realtime ASR open failed for %s with %s; buffering for batch fallback",
                serial,
                type(exc).__name__,
            )
            return _UnavailableRealtimeAsrSession(exc)

    def start_active_turn() -> bool:
        nonlocal active_asr, active_task, audio_bytes, audio_frames, audio_buffer
        nonlocal first_turn_pending
        if active_asr is None or audio_bytes == 0:
            return False
        turn_asr = active_asr
        turn_audio_duration_ms = audio_frames * 60
        turn_audio_frames = audio_buffer.copy()
        active_asr = None
        audio_bytes = 0
        audio_frames = 0
        audio_buffer.clear()
        turn_id = str(uuid.uuid4())
        strip_wake_name = first_turn_pending
        first_turn_pending = False
        active_task = asyncio.create_task(
            _process_turn(
                websocket,
                connection_lease,
                device_id,
                user_id,
                conversation_id,
                turn_id,
                snapshot,
                turn_asr,
                turn_audio_frames,
                turn_audio_duration_ms,
                history,
                time.perf_counter(),
                playback,
                noise_turn_budget,
                user_exit_event,
                telemetry_tasks,
                mcp_client,
                strip_wake_name=strip_wake_name,
            )
        )
        return True

    try:
        while True:
            incoming = await _receive_device_message(
                websocket,
                timeout_seconds=settings.device_ws_activity_timeout_seconds,
                stop_event=user_exit_event,
            )
            if user_exit_event.is_set():
                end_reason = "user-exit"
                # Persist the intentional reason before sending the close
                # frame. Some WebSocket clients stop driving the ASGI task as
                # soon as they receive that frame; the final cleanup will also
                # set ended_at and idempotently retain this reason.
                async with session_factory() as session:
                    stored_conversation = await session.get(
                        ConversationSession, conversation_id
                    )
                    if stored_conversation is not None:
                        stored_conversation.end_reason = end_reason
                        await session.commit()
                await websocket.close(code=1000, reason="user requested exit")
                break
            if incoming is None:
                end_reason = "heartbeat-timeout"
                heartbeat_timed_out = True
                logger.warning("device websocket heartbeat timeout serial=%s", serial)
                break
            if incoming.get("type") == "websocket.disconnect":
                logger.info(
                    "device websocket disconnect frame serial=%s payload=%s", serial, incoming
                )
                break
            chunk = incoming.get("bytes")
            if chunk is not None:
                if audio_frames == 0:
                    logger.info(
                        "device websocket first audio frame serial=%s bytes=%d",
                        serial,
                        len(chunk),
                    )
                # A server-VAD endpoint may arrive while the ESP32's local VAD
                # is still stuck in speech. Ignore its trailing frames once the
                # turn has moved to ASR/LLM/TTS processing.
                if active_task is not None and not active_task.done():
                    continue
                if active_asr is None:
                    logger.warning(
                        "audio arrived before listen.start for %s; opening ASR implicitly", serial
                    )
                    active_asr = await open_asr_for_turn()
                    audio_bytes = 0
                    audio_frames = 0
                    audio_buffer.clear()
                if audio_bytes + len(chunk) > MAX_UTTERANCE_BYTES:
                    await active_asr.cancel()
                    active_asr = None
                    audio_bytes = 0
                    audio_frames = 0
                    audio_buffer.clear()
                    await _send_error(
                        websocket,
                        connection_lease,
                        "audio-too-large",
                        "utterance exceeds 1 MiB",
                    )
                    continue
                if audio_frames >= settings.max_device_audio_queue_frames:
                    await active_asr.cancel()
                    active_asr = None
                    audio_bytes = 0
                    audio_frames = 0
                    audio_buffer.clear()
                    await _send_error(
                        websocket,
                        connection_lease,
                        "audio-frame-limit",
                        "utterance exceeds the configured frame limit",
                    )
                    continue
                try:
                    await active_asr.send_audio(chunk)
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        await active_asr.cancel()
                    logger.warning(
                        "realtime ASR audio send failed for %s with %s; "
                        "buffering for batch fallback",
                        serial,
                        type(exc).__name__,
                    )
                    active_asr = _UnavailableRealtimeAsrSession(exc)
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
                await _send_error(
                    websocket,
                    connection_lease,
                    "invalid-json",
                    "control message must be JSON",
                )
                continue
            message_type = message.get("type")
            logger.info("device websocket text serial=%s type=%s", serial, message_type)
            if message_type == "hello":
                features = message.get("features")
                strict_playback_ack = bool(
                    isinstance(features, dict)
                    and features.get("strict_playback_ack") is True
                )
                playback.configure(strict_ack=strict_playback_ack)
                await websocket.app.state.device_connections.send_json_for_lease(
                    connection_lease,
                    {
                        "type": "hello",
                        "transport": "websocket",
                        "version": 1,
                        "audio_params": {
                            "format": "mock-utf8"
                            if websocket.app.state.realtime_providers.mock
                            else "opus",
                            "sample_rate": 24000,
                            "channels": 1,
                            "frame_duration": 60,
                        },
                        "features": {"mcp": True, "heartbeat": True},
                        "disclosure": "你正在与 AI 服务互动，而非自然人。",
                    },
                )
                if not websocket.app.state.realtime_providers.mock:
                    mcp_client = DeviceMcpClient(
                        connection_lease, websocket.app.state.device_connections
                    )
                    mcp_initialize_task = asyncio.create_task(mcp_client.initialize())
                continue
            if message_type == "ping":
                sequence = message.get("sequence")
                if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
                    await _send_error(
                        websocket,
                        connection_lease,
                        "invalid-heartbeat",
                        "heartbeat sequence must be a non-negative integer",
                    )
                    continue
                await _record_device_heartbeat(session_factory, device_session_id)
                await websocket.app.state.device_connections.send_json_for_lease(
                    connection_lease, {"type": "pong", "sequence": sequence}
                )
                continue
            if message_type == "abort":
                first_turn_pending = False
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
                await websocket.app.state.device_connections.send_json_for_lease(
                    connection_lease, {"type": "system", "state": "aborted"}
                )
                if str(message.get("reason") or "") != "idle_timeout":
                    await websocket.app.state.device_connections.send_json_for_lease(
                        connection_lease, {"type": "llm", "emotion": "interrupted"}
                    )
                continue
            if message_type == "tts":
                state = str(message.get("state") or "")
                reply_id = str(message.get("reply_id") or "")
                turn_id = str(message.get("turn_id") or "")
                acknowledged_turn_id = turn_id or playback.turn_id or ""
                acknowledged = playback.acknowledge(state, reply_id, turn_id)
                if not acknowledged:
                    logger.info("ignored stale TTS %s acknowledgement from %s", state, serial)
                elif state == "drained" and active_task is not None:
                    end_session = await active_task
                    active_task = None
                    if telemetry_tasks:
                        await asyncio.gather(*tuple(telemetry_tasks), return_exceptions=True)
                    await websocket.app.state.device_connections.send_json_for_lease(
                        connection_lease,
                        {
                            "type": "turn",
                            "state": "completed",
                            "turn_id": acknowledged_turn_id,
                            "reply_id": reply_id,
                        },
                    )
                    if end_session:
                        user_exit_event.set()
                continue
            if message_type == "mcp":
                if mcp_client is not None and mcp_client.handle_message(message):
                    continue
                logger.info("ignored unsolicited MCP message from %s", serial)
                continue
            if message_type == "device_config_ack":
                await _handle_device_config_ack(session_factory, device_id, message)
                continue
            if message_type != "listen":
                logger.info("ignored unknown device message type %r from %s", message_type, serial)
                await _send_error(
                    websocket,
                    connection_lease,
                    "unsupported-message",
                    "unsupported control message",
                )
                continue
            state = message.get("state")
            if state == "detect":
                first_turn_pending = True
                noise_turn_budget.reset()
                continue
            if state == "start":
                if active_task is not None and not active_task.done():
                    await _send_error(
                        websocket,
                        connection_lease,
                        "turn-busy",
                        "previous turn is still active",
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
                async with session_factory() as session:
                    current_device = await session.get(Device, device_id)
                    if current_device is None:
                        await websocket.close(code=4404, reason="device removed")
                        break
                    snapshot = await _load_snapshot(session, current_device, settings)
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
                    await websocket.app.state.device_connections.send_json_for_lease(
                        connection_lease,
                        {
                            "type": "alert",
                            "status": policy.code,
                            "message": policy.message,
                        },
                    )
                    await websocket.app.state.device_connections.send_json_for_lease(
                        connection_lease, {"type": "llm", "emotion": "safe_block"}
                    )
                    active_task = asyncio.create_task(
                        _speak_fixed_message(
                            websocket,
                            connection_lease,
                            snapshot.voice,
                            snapshot.tts_speech_rate,
                            policy.message,
                            playback,
                        )
                    )
                    continue
                if policy.continuous_reminder_due and not continuous_reminder_sent:
                    reminder = "已经聊了一会儿，起来活动一下吧。"
                    await websocket.app.state.device_connections.send_json_for_lease(
                        connection_lease,
                        {
                            "type": "alert",
                            "status": "break-reminder",
                            "message": reminder,
                        },
                    )
                    active_task = asyncio.create_task(
                        _speak_fixed_message(
                            websocket,
                            connection_lease,
                            snapshot.voice,
                            snapshot.tts_speech_rate,
                            reminder,
                            playback,
                        )
                    )
                    continuous_reminder_sent = True
                    continue
                # A few ESP32 transports can put the first binary frame on the
                # socket immediately before listen.start. The binary branch
                # already opens an implicit ASR session for that race; preserve
                # it here instead of discarding the beginning of the utterance.
                if active_asr is None:
                    active_asr = await open_asr_for_turn()
                    audio_bytes = 0
                    audio_frames = 0
                    audio_buffer.clear()
                continue
            if state != "stop":
                await _send_error(
                    websocket,
                    connection_lease,
                    "invalid-listen-state",
                    "listen state must be start or stop",
                )
                continue
            if active_asr is None or audio_bytes == 0:
                if active_task is not None:
                    # The Qwen server VAD already closed this utterance. A late
                    # local listen.stop is only an acknowledgement of that turn.
                    continue
                await _send_error(
                    websocket, connection_lease, "empty-audio", "no audio received"
                )
                continue
            start_active_turn()

            if time.perf_counter() - connected_at >= 7200:
                await websocket.app.state.device_connections.send_json_for_lease(
                    connection_lease,
                    {
                        "type": "alert",
                        "status": "休息提醒",
                        "message": "你已经连续使用超过两小时，建议休息一下。",
                        "emotion": "reminder",
                    },
                )
    except WebSocketDisconnect:
        logger.info("device websocket disconnected serial=%s", serial)
    except asyncio.CancelledError:
        cancelled = True
    except Exception:
        logger.exception("device websocket failed serial=%s", serial)
    finally:
        if user_exit_event.is_set():
            end_reason = "user-exit"
        if active_asr is not None:
            await active_asr.cancel()
        if active_task is not None and not active_task.done():
            active_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await active_task
        if telemetry_tasks:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*telemetry_tasks, return_exceptions=True), timeout=2.0
                )
        await websocket.app.state.device_connections.disconnect(connection_lease)
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
        if heartbeat_timed_out:
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=1001, reason="device heartbeat timeout")
        if mcp_initialize_task is not None:
            if not mcp_initialize_task.done():
                mcp_initialize_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, DeviceMcpError):
                await mcp_initialize_task
        if mcp_client is not None:
            await mcp_client.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                _save_session_summary(websocket, conversation_id, user_id, snapshot, history),
                timeout=5.0,
            )
    if cancelled:
        raise asyncio.CancelledError
