import asyncio
import contextlib
import logging
import time

from fastapi import WebSocket

from backend.app.providers import ProviderBundle
from backend.app.quota import quota_for_user
from backend.app.safety import evaluate_text

from .emotion import EmotionRouter
from .mcp import DeviceMcpClient
from .media import OpusPacketPacer, StreamingPcmToOpus
from .messaging import send_error
from .playback import PlaybackHandshake, start_playback, stop_playback
from .providers import (
    RealtimeAsrSession,
    RealtimeProviderBundle,
    RealtimeTtsSession,
    TranscriptionResult,
    llm_for,
    open_tts_for,
)
from .snapshot import AgentSnapshot
from .tools import ToolRegistry, create_search_provider
from .turn_state import VoiceTurnState, VoiceTurnStateMachine
from .usage import record_turn

logger = logging.getLogger(__name__)


class SentenceBuffer:
    def __init__(self, max_chars: int = 48, min_clause_chars: int = 12) -> None:
        self._text = ""
        self._max_chars = max_chars
        self._min_clause_chars = min_clause_chars

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
                elif len(self._text) >= self._max_chars:
                    boundary = self._max_chars
                else:
                    break
            sentence = self._text[:boundary].strip()
            self._text = self._text[boundary:]
            if sentence:
                sentences.append(sentence)
        return sentences

    def flush(self) -> str | None:
        sentence = self._text.strip()
        self._text = ""
        return sentence or None


async def _speak_sentence(
    websocket: WebSocket,
    serial: str,
    sentence: str,
    tts: RealtimeTtsSession,
    encoder: StreamingPcmToOpus | None,
    direct_pacer: OpusPacketPacer,
) -> None:
    await websocket.app.state.device_connections.send_json(
        serial, {"type": "tts", "state": "sentence_start", "text": sentence}
    )
    async for audio in tts.synthesize(sentence):
        if encoder is None:
            if not await direct_pacer.send(audio):
                return
        else:
            await encoder.write(audio)


def _advance_policy_state(state: VoiceTurnStateMachine) -> None:
    if state.state == VoiceTurnState.IDLE:
        state.transition(VoiceTurnState.LISTENING)
    if state.state == VoiceTurnState.LISTENING:
        state.transition(VoiceTurnState.RECOGNIZING)
    if state.state == VoiceTurnState.RECOGNIZING:
        state.transition(VoiceTurnState.THINKING)
    if state.state == VoiceTurnState.THINKING:
        state.transition(VoiceTurnState.SPEAKING)


async def speak_fixed_message(
    websocket: WebSocket,
    serial: str,
    voice: str,
    speech_rate: float,
    message: str,
    playback: PlaybackHandshake,
    *,
    tts_provider: str = "dashscope",
    tts_model: str = "qwen3-tts-flash-realtime",
    turn_state: VoiceTurnStateMachine | None = None,
) -> bool:
    """Speak a product-owned policy message without invoking the LLM."""
    providers: RealtimeProviderBundle = websocket.app.state.realtime_providers
    fallback: ProviderBundle | None = websocket.app.state.fallback_providers
    tts: RealtimeTtsSession | None = None
    encoder: StreamingPcmToOpus | None = None
    packet_task: asyncio.Task[None] | None = None
    reply_id: str | None = None
    interrupted = False
    pacer = OpusPacketPacer(
        lambda packet: websocket.app.state.device_connections.send_bytes(serial, packet)
    )

    async def send_packets() -> None:
        assert encoder is not None
        async for packet in encoder.packets(prebuffer_packets=5):
            if not await pacer.send(packet):
                return

    try:
        if turn_state is not None:
            _advance_policy_state(turn_state)
        try:
            tts = await open_tts_for(
                providers, tts_provider, tts_model, voice, speech_rate
            )
        except Exception:
            if fallback is None:
                raise
            logger.warning("realtime TTS failed for policy prompt on %s; using fallback", serial)
            reply_id = await start_playback(websocket, serial, playback)
            await websocket.app.state.device_connections.send_json(
                serial, {"type": "tts", "state": "sentence_start", "text": message}
            )
            for packet in await fallback.speech.synthesize(message):
                if not await pacer.send(packet):
                    break
            return False

        reply_id = await start_playback(websocket, serial, playback)
        if not providers.mock:
            encoder = StreamingPcmToOpus(websocket.app.state.settings.gateway.ffmpeg_path)
            await encoder.start()
            packet_task = asyncio.create_task(send_packets())
        await _speak_sentence(websocket, serial, message, tts, encoder, pacer)
        await tts.finish()
        if encoder is not None:
            await encoder.finish()
        if packet_task is not None:
            await packet_task
    except asyncio.CancelledError:
        interrupted = True
        if turn_state is not None:
            turn_state.cancel()
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
            if turn_state is not None and turn_state.state == VoiceTurnState.SPEAKING:
                turn_state.transition(VoiceTurnState.DRAINING)
            await stop_playback(
                websocket,
                serial,
                playback,
                reply_id,
                wait_for_drain=not interrupted,
            )
        if turn_state is not None:
            turn_state.reset()
    return False


async def process_turn(
    websocket: WebSocket,
    serial: str,
    device_id: str,
    user_id: str,
    conversation_id: str,
    snapshot: AgentSnapshot,
    asr: RealtimeAsrSession,
    audio_frames: list[bytes],
    audio_duration_ms: int,
    history: list[dict[str, str]],
    turn_started: float,
    playback: PlaybackHandshake,
    mcp_client: DeviceMcpClient | None = None,
    turn_state: VoiceTurnStateMachine | None = None,
) -> bool:
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
    pacer = OpusPacketPacer(
        lambda packet: websocket.app.state.device_connections.send_bytes(serial, packet)
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
                raise
            error_code = getattr(exc, "code", "unknown")
            logger.warning(
                "realtime ASR failed for %s with %s; using batch fallback",
                serial,
                error_code,
            )
            fallback_operations.add("asr")
            transcript = await fallback.speech.transcribe(audio_frames)
            detected_emotion = getattr(fallback.speech, "last_emotion", None) or "neutral"
            transcription = TranscriptionResult(text=transcript, emotion=detected_emotion)
        asr_latency_ms = int((time.perf_counter() - asr_started) * 1000)
        transcript = transcription.text.strip()
        if not transcript:
            await send_error(websocket, serial, "empty-transcript", "speech was not recognized")
            return False
        await websocket.app.state.device_connections.send_json(
            serial,
            {
                "type": "stt",
                "text": transcript,
                "emotion": transcription.emotion,
            },
        )

        async with websocket.app.state.session_factory() as session:
            quota = await quota_for_user(session, user_id, websocket.app.state.settings)
        if quota.remaining <= 0:
            await send_error(
                websocket, serial, "quota-exhausted", "monthly voice quota exhausted"
            )
            return False

        safety = evaluate_text(transcript)
        emotion = router.route(transcription.emotion, safety.category)
        if turn_state is not None:
            turn_state.transition(VoiceTurnState.THINKING)
        await websocket.app.state.device_connections.send_json(
            serial, {"type": "llm", "emotion": emotion.thinking_emotion}
        )

        sentence_buffer = SentenceBuffer()
        reply_parts: list[str] = []
        spoken_parts: list[str] = []
        llm_started = time.perf_counter()
        first_sentence_at: float | None = None
        tts_started_at: float | None = None

        async def speak(sentence: str) -> None:
            nonlocal encoder, first_sentence_at, packet_task, tts, tts_started
            nonlocal batch_tts, first_audio_latency_ms, tts_started_at, reply_id
            if tts is None and not batch_tts:
                try:
                    tts = await open_tts_for(
                        providers,
                        snapshot.tts_provider,
                        snapshot.tts_model,
                        snapshot.voice,
                        snapshot.tts_speech_rate,
                    )
                except Exception:
                    if fallback is None:
                        raise
                    logger.warning("realtime TTS failed for %s; using batch fallback", serial)
                    fallback_operations.add("tts")
                    batch_tts = True
                await websocket.app.state.device_connections.send_json(
                    serial, {"type": "llm", "emotion": emotion.reply_emotion}
                )
                if turn_state is not None:
                    turn_state.transition(VoiceTurnState.SPEAKING)
                reply_id = await start_playback(websocket, serial, playback)
                if tts is not None and not providers.mock:
                    encoder = StreamingPcmToOpus(
                        websocket.app.state.settings.gateway.ffmpeg_path
                    )
                    await encoder.start()
                    packet_task = asyncio.create_task(send_packets())
                tts_started = True
                tts_started_at = time.perf_counter()
            if first_sentence_at is None:
                first_sentence_at = time.perf_counter()
            spoken_parts.append(sentence)
            if batch_tts:
                assert fallback is not None
                await websocket.app.state.device_connections.send_json(
                    serial, {"type": "tts", "state": "sentence_start", "text": sentence}
                )
                for packet in await fallback.speech.synthesize(sentence):
                    if first_audio_latency_ms is None:
                        first_audio_latency_ms = int(
                            (time.perf_counter() - turn_started) * 1000
                        )
                    if not await pacer.send(packet):
                        return
            else:
                assert tts is not None
                await _speak_sentence(websocket, serial, sentence, tts, encoder, pacer)

        if safety.fixed_response:
            reply_parts.append(safety.fixed_response)
            await speak(safety.fixed_response)
        else:
            try:
                llm_kwargs: dict[str, object] = {}
                if tool_schemas:
                    llm_kwargs = {"tools": tool_schemas, "tool_executor": execute_tool}
                async for token in llm_for(providers, snapshot.llm_provider).reply_stream(
                    transcript,
                    history,
                    snapshot.memories,
                    system_prompt=snapshot.system_prompt,
                    model=snapshot.llm_model,
                    temperature=snapshot.llm_temperature,
                    **llm_kwargs,
                ):
                    reply_parts.append(token)
                    for sentence in sentence_buffer.feed(token):
                        output_safety = evaluate_text(sentence)
                        if (
                            output_safety.fixed_response
                            and output_safety.category != "user-exit"
                        ):
                            sentence = output_safety.fixed_response
                        await speak(sentence)
            except Exception:
                if fallback is None or spoken_parts:
                    raise
                logger.warning("streaming LLM failed for %s; using batch fallback", serial)
                fallback_operations.add("llm")
                reply_parts.clear()
                sentence_buffer = SentenceBuffer()
                fallback_text = await fallback.llm.reply(transcript, snapshot.memories)
                reply_parts.append(fallback_text)
                for sentence in sentence_buffer.feed(fallback_text):
                    await speak(sentence)
            trailing = sentence_buffer.flush()
            if trailing:
                await speak(trailing)

        if not tts_started or not reply_parts:
            await send_error(websocket, serial, "empty-reply", "AI returned an empty response")
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
        llm_latency_ms = int(
            ((first_sentence_at or time.perf_counter()) - llm_started) * 1000
        )
        tts_latency_ms = int(
            (time.perf_counter() - (tts_started_at or llm_started)) * 1000
        )
        history.extend(
            [
                {"role": "user", "content": transcript},
                {"role": "assistant", "content": reply},
            ]
        )
        del history[:-20]
        await record_turn(
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
        return safety.end_session
    except asyncio.CancelledError:
        interrupted = True
        if turn_state is not None:
            turn_state.cancel()
        await asr.cancel()
        if tts is not None:
            await tts.cancel()
        if encoder is not None:
            await encoder.cancel()
        if packet_task is not None:
            packet_task.cancel()
        raise
    except Exception:
        logger.exception("voice turn failed for device %s", serial)
        await send_error(websocket, serial, "ai-unavailable", "AI response unavailable")
        return False
    finally:
        if tts_started and reply_id is not None:
            if turn_state is not None and turn_state.state == VoiceTurnState.SPEAKING:
                turn_state.transition(VoiceTurnState.DRAINING)
            await stop_playback(
                websocket,
                serial,
                playback,
                reply_id,
                wait_for_drain=not interrupted,
            )
        if turn_state is not None:
            turn_state.reset()
