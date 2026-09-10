import asyncio
import contextlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.ai.context import (
    ConfirmedMemory,
    ContextBuilder,
    ContextOverflowError,
    ContextSummary,
    LlmRequest,
    MemoryKind,
)
from backend.app.audit import add_audit_event
from backend.app.catalog import ensure_default_agent
from backend.app.config import Settings
from backend.app.device_connections import ConnectionLease
from backend.app.generated.device_ws_contract import DEVICE_STAGE_STATES
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
from backend.app.voice_routes import (
    validate_model_route,
    validate_route_admission,
    voice_is_compatible,
)

from .aliyun_dialog import AliyunDialogBackend, AliyunDialogConfig
from .conversation_backend import ConversationMessage
from .doubao import DoubaoConfig, DoubaoRealtimeBackend
from .emotion import EmotionRouter
from .face_control import SUPPORTED_FACE_EMOTIONS, FaceControlEvent, FaceControlParser
from .mcp import DeviceMcpClient, DeviceMcpError
from .media import OpusPacketPacer, StreamingOpusToPcm, StreamingPcmToOpus
from .playback import PlaybackCoordinator, PlaybackReadyTimeout
from .providers import (
    RealtimeAsrSession,
    RealtimeProviderBundle,
    RealtimeTtsSession,
    TranscriptionResult,
)
from .reply_policy import build_voice_reply_policy
from .s2s import SpeechToSpeechInput, process_s2s_turn, record_s2s_usage
from .tools import ToolRegistry, create_search_provider

logger = logging.getLogger(__name__)
telemetry_logger = logging.getLogger("uvicorn.error")
MAX_UTTERANCE_BYTES = 1024 * 1024
PLAYBACK_STARTUP_PACKETS = 5


_SPOKEN_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_SPOKEN_INTERNAL_TAG_RE = re.compile(
    r"\[(?:face|mood|emotion|state|tool|system)\s*:[^\]]*\]", re.IGNORECASE
)
_SPOKEN_EMOTION_LABEL_RE = re.compile(
    r"(?:\[\[?\s*(?:" + "|".join(sorted(SUPPORTED_FACE_EMOTIONS | {"natural"}))
    + r")\s*\]\]?|[（(]\s*(?:"
    + "|".join(sorted(SUPPORTED_FACE_EMOTIONS | {"natural"})) + r")\s*[）)])",
    re.IGNORECASE,
)
_SPOKEN_FACE_CONTROL_RE = re.compile(r"\[\[face:[^\]\r\n]*(?:\]\]|\])?", re.IGNORECASE)
_SPOKEN_STAGE_DIRECTION_RE = re.compile(
    r"[（(][^）)]{0,24}(?:点头|微笑|叹气|沉默|转身|看着|轻轻|笑)[^）)]{0,24}[）)]"
)
_LEADING_WAKE_NAME_RE = re.compile(r"^(?:你好小灿|小灿)[，,\s]*")
_NON_SPEECH_FILLER_STRIP_RE = re.compile(r"[\s，。！？!?、…,.~～]+")


@dataclass
class VoiceTurnTimeline:
    """In-memory per-turn timing only; never stores transcript or audio."""

    turn_id: str
    started_at: float
    clock: Callable[[], float] = time.perf_counter
    reply_id: str | None = None
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, stage: str, *, at: float | None = None) -> None:
        self.marks.setdefault(stage, self.clock() if at is None else at)

    def bind_reply(self, reply_id: str) -> None:
        self.reply_id = reply_id

    def mark_device_stage(
        self,
        stage: str,
        *,
        turn_id: str,
        reply_id: str,
        at: float | None = None,
    ) -> bool:
        if (
            stage != "speaker_pcm_started"
            or turn_id != self.turn_id
            or not self.reply_id
            or reply_id != self.reply_id
        ):
            return False
        self.mark("device_speaker_started", at=at)
        return True

    def elapsed_ms(self, stage: str) -> int | None:
        marked_at = self.marks.get(stage)
        if marked_at is None:
            return None
        return round((marked_at - self.started_at) * 1000)

    def as_record(
        self,
        *,
        serial: str,
        conversation_id: str,
        outcome: str,
        error_code: str | None,
        fallback_operations: set[str],
    ) -> dict[str, object]:
        stages = (
            "asr_transcription_completed",
            "asr_session_finished",
            "llm_first_token",
            "first_speakable_text",
            "tts_connected",
            "device_playback_ready",
            "tts_first_pcm",
            "gateway_first_packet",
            "device_speaker_started",
        )
        return {
            "event": "voice_turn_outcome",
            "schema_version": 1,
            "serial": serial,
            "conversation_id": conversation_id,
            "turn_id": self.turn_id,
            "reply_id": self.reply_id,
            "outcome": outcome,
            "error_code": error_code,
            "fallback_operations": sorted(fallback_operations),
            **{f"{stage}_ms": self.elapsed_ms(stage) for stage in stages},
        }


_NON_SPEECH_FILLER_CHARS = frozenset("嗯啊呃额唔哼哦")


def sanitize_spoken_text(text: str) -> str:
    """Return text suitable for TTS without leaking visual or internal markup."""
    text = _SPOKEN_URL_RE.sub("", text)
    text = _SPOKEN_FACE_CONTROL_RE.sub("", text)
    text = _SPOKEN_INTERNAL_TAG_RE.sub("", text)
    text = _SPOKEN_EMOTION_LABEL_RE.sub("", text)
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


@dataclass(frozen=True)
class AgentSnapshot:
    agent_id: str
    usage_profile_id: str
    usage_profile_kind: str
    config_version: int
    system_prompt: str
    memory_consent: bool
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
    route_kind: str = "cascade"
    realtime_provider: str | None = None
    realtime_model: str | None = None
    memory_epoch: int = 0


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
        max_chars: int = 120,
        min_clause_chars: int = 40,
        first_chunk_chars: int = 120,
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


async def _load_snapshot(session: AsyncSession, device: Device) -> AgentSnapshot:
    if device.active_agent_id is None:
        user = await session.get(User, device.owner_user_id)
        if user is None:
            raise RuntimeError("device owner is missing")
        agent = await ensure_default_agent(session, user)
        device.active_agent_id = agent.id
    else:
        agent = await session.get(Agent, device.active_agent_id)
    if agent is None or agent.owner_user_id != device.owner_user_id:
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
    validate_model_route(model)
    if not voice_is_compatible(model, voice):
        raise RuntimeError("agent voice is incompatible with its route")
    profile_memory_allowed = profile.kind == UsageProfileKind.ADULT.value or profile.memory_consent
    return AgentSnapshot(
        agent_id=agent.id,
        usage_profile_id=profile.id,
        usage_profile_kind=profile.kind,
        config_version=agent.config_version,
        memory_epoch=agent.memory_epoch,
        system_prompt=agent.system_prompt,
        memory_consent=agent.memory_consent and profile_memory_allowed,
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
        route_kind=model.route_kind,
        realtime_provider=model.realtime_provider,
        realtime_model=model.realtime_model,
    )


def _memory_kind_from_key(key: str) -> MemoryKind:
    normalized = key.lower()
    if normalized in {"name", "preferred_name", "display_name"}:
        return "name"
    if normalized.startswith("todo") or normalized.startswith("task"):
        return "todo"
    if normalized.startswith("preference") or normalized.startswith("pref"):
        return "preference"
    return "note"


async def _load_context_sources(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot: AgentSnapshot,
    settings,
) -> tuple[list[ConfirmedMemory], list[ContextSummary]]:
    """Reload consent and prompt sources for each turn so revocations apply immediately."""

    async with session_factory() as session:
        agent = await session.get(Agent, snapshot.agent_id)
        profile = await session.get(UsageProfile, snapshot.usage_profile_id)
        if agent is None or profile is None:
            return [], []
        profile_memory_allowed = (
            profile.kind == UsageProfileKind.ADULT.value or profile.memory_consent
        )
        if not agent.memory_consent or not profile_memory_allowed:
            return [], []
        memory_rows = list(
            await session.scalars(
                select(AgentMemory)
                .where(AgentMemory.agent_id == snapshot.agent_id)
                .order_by(AgentMemory.updated_at.desc(), AgentMemory.id.desc())
            )
        )
        summary_rows = list(
            await session.scalars(
                select(EncryptedSessionSummary)
                .where(EncryptedSessionSummary.agent_id == snapshot.agent_id)
                .order_by(
                    EncryptedSessionSummary.created_at.desc(),
                    EncryptedSessionSummary.id.desc(),
                )
                .limit(10)
            )
        )

    memories: list[ConfirmedMemory] = []
    for row in memory_rows:
        try:
            value = decrypt_memory(row.encrypted_value, settings)
        except Exception:
            logger.warning("ignored unreadable confirmed memory id=%s", row.id)
            continue
        memories.append(
            ConfirmedMemory(
                id=row.id,
                key=row.key,
                value=value,
                kind=_memory_kind_from_key(row.key),
                updated_at=row.updated_at,
            )
        )

    summaries: list[ContextSummary] = []
    for row in summary_rows:
        try:
            text = decrypt_memory(row.encrypted_summary, settings)
        except Exception:
            logger.warning("ignored unreadable conversation summary id=%s", row.id)
            continue
        summaries.append(ContextSummary(id=row.id, text=text, created_at=row.created_at))
    return memories, summaries


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
    playback: PlaybackCoordinator,
    turn_id: str,
    code: str,
    message: str,
) -> None:
    """Report a pre-playback turn failure without inventing an empty reply."""
    await _send_error(websocket, lease, code, message)
    playback.clear()


async def _receive_device_message(
    websocket: WebSocket,
    *,
    timeout_seconds: float,
    stop_event: asyncio.Event | None = None,
    revoked_event: asyncio.Event | None = None,
    endpoint_event: asyncio.Event | None = None,
) -> dict[str, object] | None:
    if stop_event is None and revoked_event is None and endpoint_event is None:
        try:
            return await asyncio.wait_for(websocket.receive(), timeout=timeout_seconds)
        except TimeoutError:
            return None

    receive_task = asyncio.create_task(websocket.receive())
    stop_tasks = {
        asyncio.create_task(event.wait())
        for event in (stop_event, revoked_event) if event is not None
    }
    endpoint_task = asyncio.create_task(endpoint_event.wait()) if endpoint_event else None
    watched_tasks = {receive_task, *stop_tasks}
    if endpoint_task is not None:
        watched_tasks.add(endpoint_task)
    try:
        done, _ = await asyncio.wait(
            watched_tasks,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # A control event and a device frame can arrive together. Preserve a
        # frame already consumed by receive(); otherwise the next ping is lost.
        if receive_task in done:
            return receive_task.result()
        if not done or done.intersection(stop_tasks):
            return None
        return {"type": "provider.endpoint"}
    finally:
        for task in watched_tasks:
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
    estimated_input_tokens: int,
    asr_latency_ms: int,
    llm_latency_ms: int,
    tts_latency_ms: int,
    first_audio_latency_ms: int | None,
    safety_category: str | None,
    fallback_operations: set[str],
    settings: Settings,
) -> None:
    estimated_output_tokens = max(1, len(reply) // 4)
    asr_cost = round(snapshot.asr_cost_micros_per_minute * audio_duration_ms / 60_000)
    llm_cost = round(
        snapshot.llm_input_cost_micros_per_million_tokens * estimated_input_tokens / 1_000_000
        + snapshot.llm_output_cost_micros_per_million_tokens * estimated_output_tokens / 1_000_000
    )
    tts_cost = round(snapshot.tts_cost_micros_per_10k_chars * len(reply) / 10_000)
    # The fallback has no approved price mapping here. Keep only known primary
    # estimates in the subtotal; unknown supplier charges stay explicit below.
    total_cost = sum(
        cost for operation, cost in (("asr", asr_cost), ("llm", llm_cost), ("tts", tts_cost))
        if operation not in fallback_operations
    )
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
                    provider=("dashscope-batch" if "asr" in fallback_operations
                              else snapshot.asr_provider),
                    model=(settings.fallback_asr_model if "asr" in fallback_operations
                           else snapshot.asr_model),
                    operation="asr",
                    input_units=audio_duration_ms,
                    output_units=len(transcript),
                    latency_ms=asr_latency_ms,
                    cost_micros=(
                        None if "asr" in fallback_operations or snapshot.asr_provider == "volc-asr"
                        else asr_cost
                    ),
                    cost_status=(
                        "unknown"
                        if "asr" in fallback_operations or snapshot.asr_provider == "volc-asr"
                        else "estimated"
                    ),
                    error_code=("fallback-batch" if "asr" in fallback_operations else None),
                ),
                ProviderUsage(
                    session_id=conversation_id,
                    user_id=user_id,
                    device_id=device_id,
                    provider=("dashscope-batch" if "llm" in fallback_operations
                              else snapshot.llm_provider),
                    model=(settings.fallback_llm_model if "llm" in fallback_operations
                           else snapshot.llm_model),
                    operation="llm",
                    input_units=estimated_input_tokens,
                    output_units=estimated_output_tokens,
                    latency_ms=llm_latency_ms,
                    cost_micros=(None if "llm" in fallback_operations else llm_cost),
                    cost_status=("unknown" if "llm" in fallback_operations else "estimated"),
                    error_code=("fallback-batch" if "llm" in fallback_operations else None),
                ),
                ProviderUsage(
                    session_id=conversation_id,
                    user_id=user_id,
                    device_id=device_id,
                    provider=("dashscope-batch" if "tts" in fallback_operations
                              else snapshot.tts_provider),
                    model=(settings.fallback_tts_model if "tts" in fallback_operations
                           else snapshot.tts_model),
                    operation="tts",
                    input_units=len(reply),
                    latency_ms=tts_latency_ms,
                    cost_micros=(None if "tts" in fallback_operations else tts_cost),
                    cost_status=("unknown" if "tts" in fallback_operations else "estimated"),
                    error_code=("fallback-batch" if "tts" in fallback_operations else None),
                ),
            ]
        )
        # The unsuccessful primary attempt may also be billable. Its measured
        # units are unavailable; do not duplicate the fallback's success units.
        session.add_all(
            ProviderUsage(
                session_id=conversation_id, user_id=user_id, device_id=device_id,
                provider=getattr(snapshot, f"{operation}_provider"),
                model=getattr(snapshot, f"{operation}_model"), operation=f"{operation}_attempt",
                cost_micros=None, cost_status="unknown", error_code="fallback-primary-failed",
            )
            for operation in sorted(fallback_operations)
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
    on_first_pcm: Callable[[], None] | None = None,
) -> None:
    await _send_sentence_start(websocket, lease, sentence, turn_id)
    await _synthesize_sentence_audio(
        sentence,
        tts,
        encoder,
        direct_pacer,
        on_first_pcm=on_first_pcm,
        first_audio_timeout=websocket.app.state.settings.provider_timeout_seconds,
    )


async def _send_sentence_start(
    websocket: WebSocket,
    lease: ConnectionLease,
    sentence: str,
    turn_id: str,
) -> None:
    await websocket.app.state.device_connections.send_json_for_lease(
        lease,
        {"type": "tts", "state": "sentence_start", "turn_id": turn_id, "text": sentence},
    )


async def _synthesize_sentence_audio(
    sentence: str,
    tts: RealtimeTtsSession,
    encoder: StreamingPcmToOpus | None,
    direct_pacer: OpusPacketPacer,
    *,
    on_first_pcm: Callable[[], None] | None = None,
    first_audio_timeout: float = 30,
) -> None:
    stream = aiter(tts.synthesize(sentence))
    try:
        async with asyncio.timeout(first_audio_timeout):
            audio = await anext(stream)
            while not audio:
                audio = await anext(stream)
    except StopAsyncIteration as exc:
        raise RuntimeError("tts-empty-audio") from exc
    if on_first_pcm is not None:
        on_first_pcm()
    while True:
        if encoder is None:
            if not await direct_pacer.send(audio):
                return
        else:
            await encoder.write(audio)
        try:
            audio = await anext(stream)
            while not audio:
                audio = await anext(stream)
        except StopAsyncIteration:
            return


async def _cleanup_audio_pipeline(
    tts: RealtimeTtsSession | None,
    encoder: StreamingPcmToOpus | None,
    packet_task: asyncio.Task[None] | None,
) -> None:
    if packet_task is not None:
        if not packet_task.done():
            packet_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await packet_task
    if encoder is not None:
        with contextlib.suppress(Exception):
            await encoder.cancel()
    if tts is not None:
        with contextlib.suppress(Exception):
            await tts.cancel()


async def _speak_fixed_message(
    websocket: WebSocket,
    lease: ConnectionLease,
    voice: str,
    speech_rate: float,
    message: str,
    playback: PlaybackCoordinator,
    emotion: str = "neutral",
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
    emotion_sent = False
    used_fallback = False

    async def send_audio_packet(packet: bytes) -> bool:
        nonlocal emotion_sent
        delivered = await websocket.app.state.device_connections.send_bytes_for_lease(
            lease, packet
        )
        if delivered and not emotion_sent:
            await websocket.app.state.device_connections.send_json_for_lease(
                lease,
                {"type": "llm", "emotion": emotion, "turn_id": turn_id},
            )
            emotion_sent = True
        return delivered

    pacer = OpusPacketPacer(
        send_audio_packet,
        startup_burst_packets=PLAYBACK_STARTUP_PACKETS,
    )

    async def send_packets() -> None:
        assert encoder is not None
        async for packet in encoder.packets(
            prebuffer_packets=PLAYBACK_STARTUP_PACKETS
        ):
            if not await pacer.send(packet):
                return

    try:
        try:
            if getattr(providers, "tts_provider", None) == "volc-tts":
                fallback = None
            tts = await providers.open_tts(voice, speech_rate)
        except Exception:
            if fallback is None:
                raise
            used_fallback = True
            logger.warning("realtime TTS failed for policy prompt on %s; using fallback", serial)
            reply_id = await playback.start(websocket, lease, turn_id)
            await websocket.app.state.device_connections.send_json_for_lease(
                lease,
                {"type": "tts", "state": "sentence_start", "turn_id": turn_id, "text": message},
            )
            for packet in await fallback.speech.synthesize(message):
                if not await pacer.send(packet):
                    break
            return False

        reply_id = await playback.start(websocket, lease, turn_id)
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
    except Exception as exc:
        await _cleanup_audio_pipeline(tts, encoder, packet_task)
        if (
            not isinstance(exc, PlaybackReadyTimeout)
            and fallback is not None
            and not used_fallback
            and not emotion_sent
            and await websocket.app.state.device_connections.is_current(lease)
        ):
            try:
                if reply_id is None:
                    reply_id = await playback.start(websocket, lease, turn_id)
                await _send_sentence_start(websocket, lease, message, turn_id)
                for packet in await fallback.speech.synthesize(message):
                    if packet and not await pacer.send(packet):
                        break
            except Exception:
                logger.exception("fixed policy backup failed for device %s", serial)
        else:
            logger.exception("fixed policy prompt failed for device %s", serial)
    finally:
        await _cleanup_audio_pipeline(tts, encoder, packet_task)
        if reply_id is not None:
            await playback.stop(
                websocket,
                lease,
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
    timeline: VoiceTurnTimeline,
    playback: PlaybackCoordinator,
    user_exit_event: asyncio.Event,
    telemetry_tasks: set[asyncio.Task[None]],
    mcp_client: DeviceMcpClient | None = None,
    strip_wake_name: bool = False,
    authorize: Callable[[], Awaitable[None]] | None = None,
    turn_providers: RealtimeProviderBundle | None = None,
) -> bool:
    if isinstance(asr, SpeechToSpeechInput):
        assert authorize is not None
        return await process_s2s_turn(
            websocket, lease, asr, device_id=device_id, user_id=user_id,
            conversation_id=conversation_id, snapshot=snapshot, history=history,
            timeline=timeline, playback=playback, user_exit_event=user_exit_event,
            authorize=authorize, telemetry_tasks=telemetry_tasks,
        )
    serial = lease.serial_number
    turn_started = timeline.started_at
    providers: RealtimeProviderBundle = turn_providers or websocket.app.state.realtime_providers
    fallback: ProviderBundle | None = websocket.app.state.fallback_providers
    router = EmotionRouter()
    fallback_usage_settings = websocket.app.state.settings.model_copy(deep=True)
    fallback_speech_settings = getattr(getattr(fallback, "speech", None), "settings", None)
    if fallback_speech_settings is not None:
        fallback_usage_settings = fallback_usage_settings.model_copy(update={
            "fallback_asr_model": fallback_speech_settings.asr_model,
            "fallback_tts_model": fallback_speech_settings.tts_model,
        })
    provider_settings = getattr(providers, "settings", websocket.app.state.settings)
    search_provider = create_search_provider(provider_settings)
    tool_registry = ToolRegistry(search_provider=search_provider)
    enabled_tools = {name: enabled for name, enabled in snapshot.tools.items() if enabled}
    tool_schemas = tool_registry.definitions(enabled_tools)
    if mcp_client is not None:
        tool_schemas.extend(mcp_client.openai_tools(enabled_tools))
    telemetry_logger.info("turn tools turn_id=%s names=%s", turn_id,
                          [item["function"]["name"] for item in tool_schemas])

    async def execute_tool(name: str, arguments: dict[str, object]) -> str:
        if authorize is not None:
            await authorize()
        allowed_names = {item["function"]["name"] for item in tool_schemas}
        if name not in allowed_names:
            raise RuntimeError("tool is not enabled")
        if mcp_client is not None and mcp_client.can_call(name):
            return await mcp_client.call(name, arguments)
        started = time.monotonic()
        try:
            result = await tool_registry.execute(name, arguments)
        except Exception as exc:
            telemetry_logger.info("turn tool failed turn_id=%s name=%s error=%s",
                                  turn_id, name, type(exc).__name__)
            raise
        telemetry_logger.info("turn tool completed turn_id=%s name=%s elapsed_ms=%d",
                              turn_id, name, (time.monotonic() - started) * 1000)
        return result

    tts: RealtimeTtsSession | None = None
    tts_open_task: asyncio.Task[RealtimeTtsSession] | None = None
    encoder: StreamingPcmToOpus | None = None
    encoder_start_task: asyncio.Task[StreamingPcmToOpus] | None = None
    packet_task: asyncio.Task[None] | None = None
    tts_started = False
    batch_tts = False
    first_audio_latency_ms: int | None = None
    fallback_operations: set[str] = set()
    reply_id: str | None = None
    interrupted = False
    playback_stopped = False
    pending_reply_emotion: str | None = None
    sent_reply_emotion: str | None = None
    turn_outcome = "failed"
    turn_error_code: str | None = "turn-incomplete"

    def on_first_audio_packet() -> None:
        nonlocal first_audio_latency_ms
        if first_audio_latency_ms is not None:
            return
        timeline.mark("gateway_first_packet")
        first_audio_latency_ms = int((time.perf_counter() - turn_started) * 1000)
        telemetry_logger.info(
            "tts first packet sent serial=%s lease_generation=%d turn_id=%s "
            "reply_id=%s first_audio_ms=%d",
            serial,
            lease.generation,
            turn_id,
            reply_id or "",
            first_audio_latency_ms,
        )

    async def send_reply_audio_packet(packet: bytes) -> bool:
        nonlocal sent_reply_emotion
        delivered = await websocket.app.state.device_connections.send_bytes_for_lease(
            lease, packet
        )
        if (
            delivered
            and pending_reply_emotion is not None
            and pending_reply_emotion != sent_reply_emotion
        ):
            await websocket.app.state.device_connections.send_json_for_lease(
                lease,
                {
                    "type": "llm",
                    "emotion": pending_reply_emotion,
                    "turn_id": turn_id,
                },
            )
            sent_reply_emotion = pending_reply_emotion
        return delivered

    pacer = OpusPacketPacer(
        send_reply_audio_packet,
        startup_burst_packets=PLAYBACK_STARTUP_PACKETS,
        on_first_send=on_first_audio_packet,
    )

    async def send_packets() -> None:
        assert encoder is not None
        async for packet in encoder.packets(
            prebuffer_packets=PLAYBACK_STARTUP_PACKETS
        ):
            if not await pacer.send(packet):
                return

    try:
        asr_started = time.perf_counter()
        transcription: TranscriptionResult | None = None
        try:
            transcription = await asr.finish()
            asr_finished_at = time.perf_counter()
            timeline.mark(
                "asr_transcription_completed",
                at=transcription.transcription_completed_at or asr_finished_at,
            )
            timeline.mark(
                "asr_session_finished",
                at=transcription.session_finished_at or asr_finished_at,
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                await asr.cancel()
            asr_error: Exception | None = exc
            if isinstance(asr, _UnavailableRealtimeAsrSession):
                retry_asr: RealtimeAsrSession | None = None
                try:
                    retry_asr = await providers.open_asr()
                    for frame in audio_frames:
                        await retry_asr.send_audio(frame)
                    transcription = await retry_asr.finish()
                except Exception as retry_exc:
                    asr_error = retry_exc
                    if retry_asr is not None:
                        with contextlib.suppress(Exception):
                            await retry_asr.cancel()
                else:
                    asr_error = None
                    asr_finished_at = time.perf_counter()
                    timeline.mark(
                        "asr_transcription_completed",
                        at=transcription.transcription_completed_at or asr_finished_at,
                    )
                    timeline.mark(
                        "asr_session_finished",
                        at=transcription.session_finished_at or asr_finished_at,
                    )
                    logger.info(
                        "realtime ASR reconnect succeeded for %s after replaying %d frames",
                        serial,
                        len(audio_frames),
                    )
            if asr_error is not None:
                if fallback is None or snapshot.asr_provider == "volc-asr":
                    turn_error_code = "asr-realtime-invalid"
                    await _send_turn_error_and_reset(
                        websocket,
                        lease,
                        playback,
                        turn_id,
                        "asr-realtime-invalid",
                        "realtime speech recognition is unavailable",
                    )
                    return False
                error_code = getattr(asr_error, "code", "unknown")
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
                    turn_error_code = "asr-fallback-failed"
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
                timeline.mark("asr_transcription_completed")
                timeline.mark("asr_session_finished")
        assert transcription is not None
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
            # The device owns the bounded follow-up deadline. ASR filler can be
            # caused by speaker tail or room noise and must never shorten that
            # window by forcing the device directly into standby.
            turn_outcome = "ignored"
            turn_error_code = "asr-no-speech"
            await websocket.app.state.device_connections.send_json_for_lease(
                lease,
                {"type": "listen", "state": "resume", "turn_id": turn_id},
            )
            return False
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
            turn_error_code = "quota-exhausted"
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
            turn_outcome = "control"
            turn_error_code = "user-exit"
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
            + "\n不要输出 Markdown、网址或舞台动作。根据回复的语气，在正文开头选择一个"
            "合适的表情标记；平静说明用 neutral，开心用 happy，安慰用 caring，"
            "疑惑用 confused，惊讶用 surprised，难过用 sad，生气用 angry。"
            "用户明确要求表演某种表情时，选择对应标记；表演生气不需要辱骂用户。"
            "不要为了变化强行表达情绪。"
            "如果完整句子结束后情绪明显变化，可以再输出一个标记，"
            "格式为 [[face:emotion]]，emotion 只能是 "
            + "/".join(sorted(SUPPORTED_FACE_EMOTIONS))
            + "。标记只能出现在正文开头或完整句子的边界；整次回复最多两个标记，"
            "标记之外不要输出其他内部标签。"
        )
        context_memories, context_summaries = await _load_context_sources(
            websocket.app.state.session_factory,
            snapshot,
            websocket.app.state.settings,
        )
        try:
            llm_context = ContextBuilder().build(
                system_prompt=voice_system_prompt,
                current_question=transcript,
                history=[dict(message) for message in history],
                memories=context_memories,
                summaries=context_summaries,
                tools=tool_schemas or None,
            )
        except ContextOverflowError:
            turn_error_code = "llm-context-overflow"
            await _send_turn_error_and_reset(
                websocket,
                lease,
                playback,
                turn_id,
                "llm-context-overflow",
                "conversation context is too large",
            )
            return False
        llm_request = LlmRequest(
            context=llm_context,
            model=snapshot.llm_model,
            temperature=snapshot.llm_temperature,
            tools=tool_schemas or None,
        )
        # Establish the provider TTS session while the LLM is producing its
        # first sentence. The device stays in thinking state until real PCM is
        # sent, so this hides connection setup without faking speech.
        async def open_tts_with_timing() -> RealtimeTtsSession:
            opened_tts = await providers.open_tts(
                snapshot.voice, snapshot.tts_speech_rate
            )
            timeline.mark("tts_connected")
            # Preconnect upstream only. Device start arms its first-PCM watchdog;
            # speak() starts playback once there is actual text to synthesize.
            return opened_tts

        async def start_encoder_with_timing() -> StreamingPcmToOpus:
            opened_encoder = StreamingPcmToOpus(
                websocket.app.state.settings.ffmpeg_path
            )
            await opened_encoder.start()
            timeline.mark("encoder_ready")
            return opened_encoder

        tts_open_task = asyncio.create_task(open_tts_with_timing())
        if not providers.mock:
            encoder_start_task = asyncio.create_task(start_encoder_with_timing())
        reply_parts: list[str] = []
        spoken_parts: list[str] = []
        spoken_chars = 0
        llm_started = time.perf_counter()
        first_sentence_at: float | None = None
        tts_started_at: float | None = None
        pending_reply_emotion = emotion.reply_emotion
        playback_ready = False

        def apply_face_event(event: FaceControlEvent) -> None:
            nonlocal pending_reply_emotion
            if event.kind == "emotion" and event.value in SUPPORTED_FACE_EMOTIONS:
                pending_reply_emotion = event.value

        async def speak(sentence: str) -> None:
            nonlocal encoder, first_sentence_at, packet_task, tts, tts_started
            nonlocal batch_tts, first_audio_latency_ms, tts_started_at, reply_id
            nonlocal spoken_chars
            nonlocal playback_ready
            first_realtime_segment = False
            sentence = sanitize_spoken_text(sentence)
            # Transport chunks are not sentences; only the total text budget
            # may truncate synthesis, independent of the LLM's token grouping.
            if not sentence:
                return
            sentence = sentence[: reply_policy.max_spoken_chars - spoken_chars].strip()
            if not sentence:
                return
            timeline.mark("first_speakable_text")
            if first_sentence_at is None:
                first_sentence_at = time.perf_counter()
            if tts is None and not batch_tts:
                try:
                    tts = await tts_open_task
                except Exception:
                    if fallback is None or snapshot.tts_provider == "volc-tts":
                        raise
                    logger.warning("realtime TTS failed for %s; using batch fallback", serial)
                    fallback_operations.add("tts")
                    batch_tts = True
                if reply_id is None:
                    reply_id = await playback.initiate(websocket, lease, turn_id)
                    timeline.bind_reply(reply_id)
                if tts is not None and not providers.mock:
                    assert encoder_start_task is not None
                    encoder = await encoder_start_task
                    first_realtime_segment = True
                tts_started = True
                tts_started_at = time.perf_counter()
            spoken_parts.append(sentence)
            spoken_chars += len(sentence)
            if batch_tts:
                assert fallback is not None
                assert reply_id is not None
                if not playback_ready:
                    await playback.ensure_ready(websocket, lease, reply_id, turn_id)
                    timeline.mark("device_playback_ready")
                    playback_ready = True
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
                    if packet:
                        timeline.mark("tts_first_pcm")
                    if not await pacer.send(packet):
                        return
            else:
                try:
                    assert tts is not None
                    set_emotion = getattr(tts, "set_emotion", None)
                    if set_emotion is not None:
                        await set_emotion(pending_reply_emotion or "neutral")
                    if first_realtime_segment:
                        assert encoder is not None and reply_id is not None
                        synthesis_task = asyncio.create_task(
                            _synthesize_sentence_audio(
                                sentence,
                                tts,
                                encoder,
                                pacer,
                                on_first_pcm=lambda: timeline.mark("tts_first_pcm"),
                                first_audio_timeout=websocket.app.state.settings.provider_timeout_seconds,
                            )
                        )
                        try:
                            await playback.ensure_ready(
                                websocket, lease, reply_id, turn_id
                            )
                            timeline.mark("device_playback_ready")
                            playback_ready = True
                            await _send_sentence_start(websocket, lease, sentence, turn_id)
                            packet_task = asyncio.create_task(send_packets())
                            await synthesis_task
                        except BaseException:
                            if not synthesis_task.done():
                                synthesis_task.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await synthesis_task
                            raise
                    else:
                        assert reply_id is not None
                        if not playback_ready:
                            await playback.ensure_ready(
                                websocket, lease, reply_id, turn_id
                            )
                            timeline.mark("device_playback_ready")
                            playback_ready = True
                        await _speak_sentence(
                            websocket,
                            lease,
                            sentence,
                            tts,
                            encoder,
                            pacer,
                            turn_id,
                            on_first_pcm=lambda: timeline.mark("tts_first_pcm"),
                        )
                except PlaybackReadyTimeout:
                    raise
                except Exception:
                    if (fallback is None or snapshot.tts_provider == "volc-tts"
                            or "gateway_first_packet" in timeline.marks):
                        raise
                    if authorize is not None:
                        await authorize()
                    # Nothing was sent: discard buffered PCM before retrying the
                    # complete unheard prefix on the existing cascade backup.
                    await _cleanup_audio_pipeline(tts, encoder, packet_task)
                    tts, encoder, packet_task = None, None, None
                    batch_tts = True
                    fallback_operations.add("tts")
                    for packet in await fallback.speech.synthesize("".join(spoken_parts)):
                        if packet and not await pacer.send(packet):
                            return

        if safety.fixed_response:
            reply_parts.append(safety.fixed_response)
            await speak(safety.fixed_response)
        else:
            try:
                async for token in providers.llm.reply_stream(
                    llm_request,
                    tool_executor=execute_tool if tool_schemas else None,
                ):
                    timeline.mark("llm_first_token")
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
            except PlaybackReadyTimeout:
                raise
            except Exception:
                if fallback is None or spoken_parts or tts_started or tts is not None:
                    raise
                logger.warning("streaming LLM failed for %s; using batch fallback", serial)
                fallback_operations.add("llm")
                reply_parts.clear()
                sentence_buffer = SentenceBuffer()
                fallback_text = await fallback.llm.reply(
                    llm_request.with_model(fallback_usage_settings.fallback_llm_model)
                )
                reply_parts.append(fallback_text)
                for sentence in sentence_buffer.feed(fallback_text):
                    await speak(sentence)
            trailing = sentence_buffer.flush()
            if trailing:
                await speak(trailing)

        if not tts_started or not reply_parts:
            turn_error_code = "empty-reply"
            if reply_id is not None and playback.reply_id == reply_id:
                await playback.stop(
                    websocket,
                    lease,
                    reply_id,
                    turn_id,
                    wait_for_drain=False,
                )
                playback_stopped = True
                await _send_error(
                    websocket, lease, "empty-reply", "AI returned an empty response"
                )
            else:
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

        reply = "".join(spoken_parts).strip()
        telemetry_logger.info(
            "voice text budget turn_id=%s generated_chars=%d spoken_chars=%d limit=%d",
            turn_id, len("".join(reply_parts)), len(reply), reply_policy.max_spoken_chars,
        )
        llm_latency_ms = int(((first_sentence_at or time.perf_counter()) - llm_started) * 1000)
        tts_latency_ms = int((time.perf_counter() - (tts_started_at or llm_started)) * 1000)

        assert reply_id is not None
        drain_started = time.perf_counter()
        drain_outcome = await playback.stop(
            websocket,
            lease,
            reply_id,
            turn_id,
            wait_for_drain=True,
        )
        playback_stopped = True
        logger.info(
            "voice turn drain result serial=%s turn_id=%s delivered=%s "
            "acknowledged=%s compatibility_accepted=%s wait_ms=%d",
            serial,
            turn_id,
            drain_outcome.delivered,
            drain_outcome.acknowledged,
            drain_outcome.compatibility_accepted,
            int((time.perf_counter() - drain_started) * 1000),
        )
        telemetry_logger.info(
            "voice turn timeline serial=%s turn_id=%s reply_id=%s "
            "asr_transcription_completed_ms=%s asr_session_finished_ms=%s "
            "llm_first_token_ms=%s first_speakable_text_ms=%s "
            "tts_connected_ms=%s device_playback_ready_ms=%s "
            "tts_first_pcm_ms=%s gateway_first_packet_ms=%s "
            "device_speaker_started_ms=%s fallbacks=%d",
            serial,
            turn_id,
            reply_id,
            timeline.elapsed_ms("asr_transcription_completed"),
            timeline.elapsed_ms("asr_session_finished"),
            timeline.elapsed_ms("llm_first_token"),
            timeline.elapsed_ms("first_speakable_text"),
            timeline.elapsed_ms("tts_connected"),
            timeline.elapsed_ms("device_playback_ready"),
            timeline.elapsed_ms("tts_first_pcm"),
            timeline.elapsed_ms("gateway_first_packet"),
            timeline.elapsed_ms("device_speaker_started"),
            len(fallback_operations),
        )
        if not drain_outcome:
            turn_error_code = "tts-drained-timeout"
            await _send_error(
                websocket,
                lease,
                "tts-drained-timeout",
                "audio playback did not finish cleanly",
            )
            return False
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
                estimated_input_tokens=llm_context.estimated_input_tokens,
                asr_latency_ms=asr_latency_ms,
                llm_latency_ms=llm_latency_ms,
                tts_latency_ms=tts_latency_ms,
                first_audio_latency_ms=first_audio_latency_ms,
                safety_category=safety.category,
                fallback_operations=fallback_operations,
                settings=fallback_usage_settings,
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
        if not playback.strict_ack and not playback.last_drain_acknowledged:
            # Legacy devices never send drained. Their one-second compatibility
            # wait has already elapsed, so finish the telemetry handoff here
            # instead of keeping the turn task alive until the socket closes.
            await asyncio.gather(telemetry_task, return_exceptions=True)
            await websocket.app.state.device_connections.send_json_for_lease(
                lease,
                {
                    "type": "turn",
                    "state": "completed",
                    "turn_id": turn_id,
                    "reply_id": reply_id,
                },
            )
        turn_outcome = "completed"
        turn_error_code = None
        timeline.mark("turn_completed")
        return safety.end_session
    except asyncio.CancelledError:
        interrupted = True
        turn_outcome = "interrupted"
        turn_error_code = "turn-cancelled"
        await asr.cancel()
        raise
    except PlaybackReadyTimeout:
        code = "tts-ready-timeout"
        turn_error_code = code
        logger.warning(
            "voice turn timed out for device %s lease_generation=%d code=%s",
            serial,
            lease.generation,
            code,
        )
        await _send_error(
            websocket, lease, code, "audio playback handshake timed out"
        )
        await websocket.app.state.device_connections.retire(
            lease, code=1011, reason="tts ready timeout"
        )
        return False
    except Exception:
        turn_error_code = "ai-unavailable"
        logger.exception("voice turn failed for device %s", serial)
        await _cleanup_audio_pipeline(tts, encoder, packet_task)
        if reply_id is not None or tts_started:
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
        telemetry_logger.info(
            "voice turn outcome %s",
            json.dumps(
                timeline.as_record(
                    serial=serial,
                    conversation_id=conversation_id,
                    outcome=turn_outcome,
                    error_code=turn_error_code,
                    fallback_operations=fallback_operations,
                ),
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        if reply_id is not None and not playback_stopped and playback.reply_id == reply_id:
            with contextlib.suppress(Exception):
                await playback.stop(
                    websocket,
                    lease,
                    reply_id,
                    turn_id,
                    wait_for_drain=not interrupted,
                )
            playback_stopped = True
        await _cleanup_audio_pipeline(tts, encoder, packet_task)
        if encoder is None and encoder_start_task is not None:
            if not encoder_start_task.done():
                encoder_start_task.cancel()
            try:
                unused_encoder = await encoder_start_task
            except (asyncio.CancelledError, Exception):
                pass
            else:
                with contextlib.suppress(Exception):
                    await unused_encoder.cancel()
        if tts is None and tts_open_task is not None:
            if not tts_open_task.done():
                tts_open_task.cancel()
            try:
                unused_tts = await tts_open_task
            except (asyncio.CancelledError, Exception):
                pass
            else:
                await unused_tts.cancel()
        if reply_id is not None and not playback_stopped and playback.reply_id == reply_id:
            with contextlib.suppress(Exception):
                await playback.stop(
                    websocket,
                    lease,
                    reply_id,
                    turn_id,
                    wait_for_drain=not interrupted,
                )
            playback_stopped = True


async def _save_session_summary(
    websocket: WebSocket,
    conversation_id: str,
    user_id: str,
    snapshot: AgentSnapshot,
    history: list[dict[str, str]],
) -> None:
    if not snapshot.memory_consent or not history:
        return
    async with websocket.app.state.session_factory() as session:
        current = await session.get(Agent, snapshot.agent_id)
        conversation = await session.get(ConversationSession, conversation_id)
        device = await session.get(Device, conversation.device_id) if conversation else None
        if (current is None or not current.memory_consent
                or current.owner_user_id != user_id
                or current.memory_epoch != snapshot.memory_epoch
                or conversation is None or conversation.user_id != user_id
                or device is None or device.owner_user_id != user_id):
            return
    prompt = "请把这次对话概括为不超过120字的偏好和待办摘要，不要记录敏感原文。"
    parts: list[str] = []
    try:
        summary_context = ContextBuilder().build(
            system_prompt="只输出简短、客观的会话摘要。",
            current_question=prompt,
            history=[dict(message) for message in history],
            memories=[],
            summaries=[],
            tools=None,
        )
        summary_request = LlmRequest(
            context=summary_context,
            model=snapshot.llm_model or websocket.app.state.settings.llm_model,
            temperature=0.2,
            max_output_tokens=256,
        )
        async for token in websocket.app.state.realtime_providers.llm.reply_stream(
            summary_request,
        ):
            parts.append(token)
        summary = "".join(parts).strip()[:500]
        if not summary:
            return
        async with websocket.app.state.session_factory() as session:
            fence = await session.execute(
                update(Agent).where(
                    Agent.id == snapshot.agent_id,
                    Agent.owner_user_id == user_id,
                    Agent.memory_consent.is_(True),
                    Agent.memory_epoch == snapshot.memory_epoch,
                ).values(memory_epoch=Agent.memory_epoch)
            )
            if fence.rowcount != 1:
                return
            conversation = await session.get(ConversationSession, conversation_id)
            device = await session.get(Device, conversation.device_id) if conversation else None
            agent = await session.get(Agent, snapshot.agent_id)
            profile = await session.get(UsageProfile, snapshot.usage_profile_id)
            profile_memory_allowed = bool(
                profile
                and (profile.kind == UsageProfileKind.ADULT.value or profile.memory_consent)
            )
            if (
                conversation is None
                or conversation.user_id != user_id
                or device is None
                or device.owner_user_id != user_id
                or agent is None
                or not agent.memory_consent
                or not profile_memory_allowed
            ):
                logger.info(
                    "discarded completed summary after consent change conversation=%s "
                    "code=memory-consent-revoked",
                    conversation_id,
                )
                return
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
        snapshot = await _load_snapshot(session, device)
        await session.flush()
        connection_id = str(uuid.uuid4())
        device_session = DeviceSession(
            device_id=device.id,
            gateway_id=settings.gateway_id,
            connection_id=connection_id,
            firmware_version=device.firmware_version,
        )
        session.add(device_session)
        device.last_seen_at = datetime.now(UTC)
        await session.commit()
        device_id = device.id
        user_id = device.owner_user_id
        reset_epoch = device.reset_epoch
        device_session_id = device_session.id

    await websocket.accept()
    logger.info(
        "device websocket accepted serial=%s connection_id=%s auth=%s",
        serial,
        connection_id,
        "token" if valid_token else "secret",
    )
    connection_lease = await websocket.app.state.device_connections.connect(
        serial, websocket, connection_id, reset_epoch=reset_epoch
    )
    active_asr: RealtimeAsrSession | SpeechToSpeechInput | None = None
    turn_providers = websocket.app.state.realtime_providers
    active_task: asyncio.Task[bool] | None = None
    active_timeline: VoiceTurnTimeline | None = None
    audio_bytes = 0
    audio_frames = 0
    audio_buffer: list[bytes] = []
    conversation_id: str | None = None
    conversation_started_at: datetime | None = None
    history: list[dict[str, str]] = []
    aliyun_previous: AliyunDialogBackend | None = None
    cancelled = False
    heartbeat_timed_out = False
    continuous_reminder_sent = False
    playback = PlaybackCoordinator()
    user_exit_event = asyncio.Event()
    telemetry_tasks: set[asyncio.Task[None]] = set()
    summary_tasks: set[asyncio.Task[None]] = set()
    last_summary_task: asyncio.Task[None] | None = None
    mcp_client: DeviceMcpClient | None = None
    mcp_initialize_task: asyncio.Task[None] | None = None
    # A warm device WebSocket can span multiple sleep/wake sessions. Only a
    # real local wake arms prefix removal for the immediately following turn.
    first_turn_pending = False
    last_input_closed = False

    def asr_endpoint_detected(asr: RealtimeAsrSession) -> bool:
        detector = getattr(asr, "endpoint_detected", None)
        return bool(detector and detector())

    async def require_current_ownership(session: AsyncSession) -> Device:
        current = await session.get(Device, device_id)
        if (
            current is None
            or current.owner_user_id != user_id
            or current.reset_epoch != reset_epoch
            or current.lifecycle != DeviceLifecycle.OWNED.value
            or not await websocket.app.state.device_connections.is_current(connection_lease)
        ):
            await websocket.app.state.device_connections.retire(
                connection_lease, code=4403, reason="device ownership revoked"
            )
            raise WebSocketDisconnect(code=4403)
        return current

    async def authorize_turn() -> None:
        async with session_factory() as session:
            await require_current_ownership(session)

    async def open_asr_for_turn() -> RealtimeAsrSession | SpeechToSpeechInput | None:
        """One entry for explicit and early-binary input, including policy and route selection."""
        nonlocal snapshot, active_task, continuous_reminder_sent, turn_providers
        nonlocal last_input_closed
        nonlocal aliyun_previous
        last_input_closed = False
        async with session_factory() as session:
            current_device = await require_current_ownership(session)
            next_snapshot = await _load_snapshot(session, current_device)
            if (next_snapshot.agent_id != snapshot.agent_id
                    or next_snapshot.usage_profile_id != snapshot.usage_profile_id
                    or next_snapshot.config_version != snapshot.config_version
                    or next_snapshot.memory_epoch != snapshot.memory_epoch):
                aliyun_previous = None
            if conversation_id is not None and (
                next_snapshot.agent_id != snapshot.agent_id
                or next_snapshot.usage_profile_id != snapshot.usage_profile_id
                or next_snapshot.memory_epoch != snapshot.memory_epoch
            ):
                await finalize_logical_conversation("configuration-changed")
            snapshot = next_snapshot
            profile = await session.get(UsageProfile, snapshot.usage_profile_id)
            if profile is None:
                raise WebSocketDisconnect(code=4403)
            await ensure_logical_conversation()
            policy = await evaluate_profile_policy(
                session, profile, family_mode_enabled=settings.family_mode_enabled,
                conversation_started_at=conversation_started_at,
            )
            quota = await quota_for_user(session, user_id, settings)
            await session.commit()
        if not policy.allowed or (policy.continuous_reminder_due and not continuous_reminder_sent):
            reminder = policy.allowed
            message = "已经聊了一会儿，起来活动一下吧。" if reminder else policy.message
            await websocket.app.state.device_connections.send_json_for_lease(
                connection_lease, {"type": "alert",
                                   "status": "break-reminder" if reminder else policy.code,
                                   "message": message},
            )
            if snapshot.route_kind == "cascade":
                active_task = asyncio.create_task(_speak_fixed_message(
                    websocket, connection_lease, snapshot.voice, snapshot.tts_speech_rate,
                    message, playback, emotion="safe_block" if not reminder else "neutral",
                ))
            continuous_reminder_sent = continuous_reminder_sent or reminder
            return None
        if quota.remaining <= 0:
            await _send_error(websocket, connection_lease, "quota-exhausted",
                              "monthly voice quota exhausted")
            return None
        try:
            validate_route_admission(snapshot, settings)
        except ValueError:
            await _send_error(websocket, connection_lease, "s2s-not-enabled",
                              "所选语音方案尚未开放，请手动选择其他方案。")
            return None
        if snapshot.route_kind == "managed_app":
            if snapshot.usage_profile_kind != UsageProfileKind.ADULT.value:
                await _send_error(websocket, connection_lease, "s2s-not-enabled",
                                  "阿里应用尚未开放，请手动选择其他语音方案。")
                return None
            backend = None
            decoder = None
            try:
                opening_at = time.monotonic()
                backend = await AliyunDialogBackend.open(AliyunDialogConfig(
                    api_key=settings.aliyun_dialog_api_key, url=settings.aliyun_dialog_url,
                    workspace_id=settings.aliyun_dialog_workspace_id,
                    app_id=settings.aliyun_dialog_app_id,
                    timeout_seconds=settings.provider_timeout_seconds,
                    dialog_id=(aliyun_previous.completed_dialog_id
                               if aliyun_previous is not None and history else ""),
                    client_id=(aliyun_previous.config.client_id
                               if aliyun_previous is not None and history else uuid.uuid4().hex),
                ))
                aliyun_previous = backend
                decoder = StreamingOpusToPcm(settings.ffmpeg_path)
                await decoder.start()
                source = SpeechToSpeechInput(backend, decoder, str(uuid.uuid4()))
                telemetry_logger.info("voice input turn=%s upstream_open_ms=%d", source.turn_id,
                            round((time.monotonic()-opening_at)*1000))
                return source
            except BaseException as exc:
                if decoder is not None:
                    await decoder.cancel()
                if backend is not None:
                    await backend.close()
                if not isinstance(exc, Exception):
                    raise
                await _send_error(websocket, connection_lease,
                                  getattr(exc, "code", "s2s-unavailable"),
                                  "阿里应用暂时不可用，请重试或手动更换语音方案。")
                return None
        if snapshot.route_kind == "realtime_s2s":
            if snapshot.usage_profile_kind != UsageProfileKind.ADULT.value:
                await _send_error(websocket, connection_lease, "s2s-not-enabled",
                                  "豆包语音尚未开放，请手动选择其他语音方案。")
                return None
            backend = None
            decoder = None
            try:
                registry = ToolRegistry(search_provider=create_search_provider(settings))
                tools = registry.definitions(snapshot.tools)
                if mcp_client is not None:
                    tools.extend(mcp_client.openai_tools(snapshot.tools))
                allowed_names = {item["function"]["name"] for item in tools}
                input_accepted = asyncio.Event()

                async def execute_tool(name, arguments):
                    await asyncio.wait_for(input_accepted.wait(), settings.provider_timeout_seconds)
                    await authorize_turn()
                    async with session_factory() as session:
                        agent = await session.get(Agent, snapshot.agent_id)
                        current_tools = json.loads(agent.tools_json or "{}") if agent else {}
                    permission_name = DeviceMcpClient.SAFE_TOOL_ALIASES.get(name, name)
                    if name not in allowed_names or not current_tools.get(permission_name, False):
                        raise RuntimeError("tool is not enabled")
                    if mcp_client is not None and mcp_client.can_call(name):
                        return await mcp_client.call(name, arguments)
                    return await registry.execute(name, arguments)

                memories, summaries = await _load_context_sources(
                    session_factory, snapshot, settings,
                )
                context = ContextBuilder().build(
                    system_prompt=(snapshot.system_prompt + "\n"
                                   + build_voice_reply_policy("").context
                                   + "\n用户明确要故事时讲完整短故事，最多160字。"
                                   "不要输出或朗读Markdown、网址、表情标签或舞台动作。"),
                    current_question="", history=history[-20:], memories=memories,
                    summaries=summaries, tools=tools or None,
                )
                instructions = "\n".join(str(item["content"]) for item in context.messages
                                         if item["role"] == "system")
                previous = [ConversationMessage(role=item["role"], text=str(item["content"]))
                            for item in context.messages[:-1]
                            if item["role"] in {"user", "assistant"}]
                # ponytail: fresh upstream per turn keeps consent/context isolation;
                # reuse only after measuring connection cost with equivalent invalidation.
                backend = await DoubaoRealtimeBackend.open(
                    DoubaoConfig(api_key=settings.doubao_api_key, url=settings.doubao_realtime_url,
                                 model=snapshot.realtime_model, voice=snapshot.voice,
                                 timeout_seconds=settings.provider_timeout_seconds),
                    instructions=instructions, history=previous, tools=tools,
                    tool_executor=execute_tool if tools else None,
                )
                decoder = StreamingOpusToPcm(settings.ffmpeg_path)
                await decoder.start()
                return SpeechToSpeechInput(backend, decoder, str(uuid.uuid4()), input_accepted)
            except asyncio.CancelledError:
                if decoder is not None:
                    await decoder.cancel()
                if backend is not None:
                    await backend.close()
                raise
            except Exception as exc:
                if decoder is not None:
                    await decoder.cancel()
                if backend is not None:
                    await backend.close()
                await _send_error(websocket, connection_lease,
                                  getattr(exc, "code", "s2s-unavailable"),
                                  "豆包语音暂时不可用，请重试或手动更换语音方案。")
                return None
        turn_providers = websocket.app.state.realtime_providers
        if isinstance(turn_providers, RealtimeProviderBundle):
            turn_providers = turn_providers.for_models(
                asr_provider=snapshot.asr_provider, asr_model=snapshot.asr_model,
                llm_provider=snapshot.llm_provider, llm_model=snapshot.llm_model,
                tts_provider=snapshot.tts_provider, tts_model=snapshot.tts_model,
            )
        try:
            return await turn_providers.open_asr()
        except Exception as exc:
            logger.warning(
                "realtime ASR open failed for %s with %s; buffering for batch fallback",
                serial,
                type(exc).__name__,
            )
            return _UnavailableRealtimeAsrSession(exc)

    async def ensure_logical_conversation() -> str:
        nonlocal conversation_id, conversation_started_at
        nonlocal continuous_reminder_sent, last_summary_task
        if conversation_id is not None:
            return conversation_id
        if last_summary_task is not None and not last_summary_task.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(last_summary_task), timeout=0.5)
        async with session_factory() as session:
            conversation = ConversationSession(
                user_id=user_id,
                agent_id=snapshot.agent_id,
                device_id=device_id,
                usage_profile_id=snapshot.usage_profile_id,
            )
            session.add(conversation)
            await session.commit()
            conversation_id = conversation.id
            conversation_started_at = conversation.started_at
        history.clear()
        continuous_reminder_sent = False
        return conversation_id

    async def finalize_logical_conversation(reason: str) -> None:
        nonlocal aliyun_previous
        aliyun_previous = None
        nonlocal conversation_id, conversation_started_at
        nonlocal continuous_reminder_sent, last_summary_task
        if conversation_id is None:
            return
        finalized_id = conversation_id
        finalized_history = history.copy()
        finalized_snapshot = snapshot
        conversation_id = None
        conversation_started_at = None
        history.clear()
        continuous_reminder_sent = False
        async with session_factory() as session:
            conversation = await session.get(ConversationSession, finalized_id)
            if conversation is not None and conversation.ended_at is None:
                conversation.ended_at = datetime.now(UTC)
                conversation.end_reason = reason[:64]
            await session.commit()
        if not finalized_snapshot.memory_consent or not finalized_history:
            return
        summary_task = asyncio.create_task(
            _save_session_summary(
                websocket,
                finalized_id,
                user_id,
                finalized_snapshot,
                finalized_history,
            )
        )
        last_summary_task = summary_task
        summary_tasks.add(summary_task)

        def observe_summary(task: asyncio.Task[None]) -> None:
            summary_tasks.discard(task)
            try:
                task.result()
            except Exception:
                logger.exception("background summary failed for conversation %s", finalized_id)

        summary_task.add_done_callback(observe_summary)

    async def cancel_input() -> None:
        nonlocal active_asr
        source, active_asr = active_asr, None
        if source is None:
            return
        try:
            await source.cancel()
        finally:
            # A paid upstream can be aborted before ASR/VAD starts a reply task.
            # Keep that attempt visible without consuming a completed-turn quota.
            if isinstance(source, SpeechToSpeechInput) and conversation_id is not None:
                try:
                    await record_s2s_usage(
                        session_factory, conversation_id=conversation_id, user_id=user_id,
                        device_id=device_id, model=snapshot.realtime_model,
                        provider_session_id=source.backend.session_id, response_id="",
                        turn_id=source.turn_id, usage={}, completed=False,
                        latency_ms=None, error_code="input-cancelled",
                        provider=snapshot.realtime_provider,
                        operation=("managed_dialog" if snapshot.route_kind == "managed_app"
                                   else "realtime_s2s"),
                    )
                except Exception:
                    logger.error("S2S input usage recording failed turn_id=%s", source.turn_id)

    async def start_active_turn(turn_started: float) -> bool:
        nonlocal active_asr, active_task, audio_bytes, audio_frames, audio_buffer
        nonlocal active_timeline, first_turn_pending
        nonlocal last_input_closed
        if active_asr is None or audio_bytes == 0:
            return False
        last_input_closed = True
        async with session_factory() as session:
            await require_current_ownership(session)
        active_conversation_id = await ensure_logical_conversation()
        turn_asr = active_asr
        turn_audio_duration_ms = audio_frames * 60
        turn_audio_frames = audio_buffer.copy()
        active_asr = None
        audio_bytes = 0
        audio_frames = 0
        audio_buffer.clear()
        turn_id = (
            turn_asr.turn_id if isinstance(turn_asr, SpeechToSpeechInput) else str(uuid.uuid4())
        )
        active_timeline = VoiceTurnTimeline(turn_id=turn_id, started_at=turn_started)
        strip_wake_name = first_turn_pending
        first_turn_pending = False
        active_task = asyncio.create_task(
            _process_turn(
                websocket,
                connection_lease,
                device_id,
                user_id,
                active_conversation_id,
                turn_id,
                snapshot,
                turn_asr,
                turn_audio_frames,
                turn_audio_duration_ms,
                history,
                active_timeline,
                playback,
                user_exit_event,
                telemetry_tasks,
                mcp_client,
                strip_wake_name=strip_wake_name,
                authorize=authorize_turn,
                turn_providers=turn_providers,
            )
        )
        return True

    try:
        while True:
            incoming = await _receive_device_message(
                websocket,
                timeout_seconds=settings.device_ws_activity_timeout_seconds,
                stop_event=user_exit_event,
                revoked_event=connection_lease.revoked,
                endpoint_event=getattr(active_asr, "endpoint_event", None),
            )
            if connection_lease.revoked.is_set():
                break
            if incoming is not None and incoming.get("type") == "provider.endpoint":
                if not await start_active_turn(time.perf_counter()) and active_asr is not None:
                    await cancel_input()
                    active_asr = None
                    await _send_error(websocket, connection_lease, "s2s-input-unavailable",
                                      "语音输入未完成，请重试。")
                continue
            if user_exit_event.is_set():
                await finalize_logical_conversation("user-exit")
                user_exit_event.clear()
                first_turn_pending = False
                if incoming is None:
                    continue
            if incoming is None:
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
                    if active_asr is None:
                        continue
                if audio_bytes + len(chunk) > MAX_UTTERANCE_BYTES:
                    await cancel_input()
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
                    await cancel_input()
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
                    if isinstance(active_asr, SpeechToSpeechInput):
                        await cancel_input()
                        active_asr = None
                        audio_buffer.clear()
                        audio_bytes = audio_frames = 0
                        await _send_error(websocket, connection_lease, "s2s-input-failed",
                                          "豆包收音暂时不可用，请重试。")
                        continue
                    with contextlib.suppress(Exception):
                        await cancel_input()
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
                    await start_active_turn(time.perf_counter())
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
            message_received_at = time.perf_counter()
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
                            and snapshot.route_kind == "cascade"
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
                abort_reason = str(message.get("reason") or "aborted")
                first_turn_pending = False
                if active_asr is not None:
                    await cancel_input()
                    active_asr = None
                if active_task is not None and not active_task.done():
                    active_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await active_task
                active_task = None
                audio_bytes = 0
                audio_frames = 0
                audio_buffer.clear()
                playback.clear()
                await finalize_logical_conversation(abort_reason)
                await websocket.app.state.device_connections.send_json_for_lease(
                    connection_lease, {"type": "system", "state": "aborted"}
                )
                if abort_reason != "idle_timeout":
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
                    if active_timeline is None or "turn_completed" in active_timeline.marks:
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
                        await finalize_logical_conversation("user-exit")
                        user_exit_event.clear()
                continue
            if message_type == "mcp":
                if mcp_client is not None and mcp_client.handle_message(message):
                    continue
                logger.info("ignored unsolicited MCP message from %s", serial)
                continue
            if message_type == "device_config_ack":
                await _handle_device_config_ack(session_factory, device_id, message)
                continue
            if message_type == "device_stage":
                stage = str(message.get("stage") or "")
                if stage not in DEVICE_STAGE_STATES:
                    await _send_error(
                        websocket,
                        connection_lease,
                        "invalid-device-stage",
                        "unsupported device stage",
                    )
                    continue
                stage_turn_id = str(message.get("turn_id") or "")
                stage_reply_id = str(message.get("reply_id") or "")
                accepted = stage != "speaker_pcm_started"
                if stage == "speaker_pcm_started" and active_timeline is not None:
                    accepted = active_timeline.mark_device_stage(
                        stage,
                        turn_id=stage_turn_id,
                        reply_id=stage_reply_id,
                        at=message_received_at,
                    )
                logger.info(
                    "device stage serial=%s stage=%s turn_id=%s reply_id=%s accepted=%s",
                    serial,
                    stage,
                    stage_turn_id,
                    stage_reply_id,
                    accepted,
                )
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
                await ensure_logical_conversation()
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
                        await finalize_logical_conversation("user-exit")
                        user_exit_event.clear()
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
            if state == "standby":
                if active_asr is not None:
                    await cancel_input()
                    active_asr = None
                if active_task is not None and not active_task.done():
                    active_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await active_task
                active_task = None
                active_timeline = None
                audio_bytes = 0
                audio_frames = 0
                audio_buffer.clear()
                playback.clear()
                await finalize_logical_conversation(
                    str(message.get("reason") or "standby")
                )
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
                if active_task is not None or last_input_closed:
                    # The Qwen server VAD already closed this utterance. A late
                    # local listen.stop is only an acknowledgement of that turn.
                    continue
                await _send_error(
                    websocket, connection_lease, "empty-audio", "no audio received"
                )
                continue
            await start_active_turn(message_received_at)

    except WebSocketDisconnect:
        logger.info("device websocket disconnected serial=%s", serial)
    except asyncio.CancelledError:
        cancelled = True
        current_task = asyncio.current_task()
        if current_task is not None:
            while current_task.cancelling():
                current_task.uncancel()
    except Exception:
        logger.exception("device websocket failed serial=%s", serial)
    finally:
        async def cleanup_connection() -> None:
            if active_asr is not None:
                await cancel_input()
            if active_task is not None and not active_task.done():
                active_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await active_task
            if telemetry_tasks:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        asyncio.gather(*telemetry_tasks, return_exceptions=True), timeout=2.0
                    )
            disconnect_reason = "cancelled" if cancelled else "disconnected"
            if heartbeat_timed_out:
                disconnect_reason = "heartbeat-timeout"
            elif user_exit_event.is_set():
                disconnect_reason = "user-exit"
            if active_task is not None and active_task.done():
                with contextlib.suppress(Exception):
                    if active_task.result():
                        disconnect_reason = "user-exit"
            await finalize_logical_conversation(disconnect_reason)
            await websocket.app.state.device_connections.disconnect(connection_lease)
            now = datetime.now(UTC)
            async with session_factory() as session:
                stored_device_session = await session.get(DeviceSession, device_session_id)
                if stored_device_session is not None:
                    stored_device_session.status = DeviceSessionStatus.OFFLINE.value
                    stored_device_session.disconnected_at = now
                    stored_device_session.heartbeat_at = now
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
            if summary_tasks:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        asyncio.gather(*tuple(summary_tasks), return_exceptions=True),
                        timeout=5.0,
                    )

        cleanup_task = asyncio.create_task(cleanup_connection())
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None:
                    while current_task.cancelling():
                        current_task.uncancel()
        await cleanup_task
