"""Speech-to-speech turn orchestration using the existing device playback path."""

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.models import ConversationSession, ProviderUsage, UsageEvent
from backend.app.safety import evaluate_text
from backend.app.schemas import ProviderUsageDetails

from .conversation_backend import ConversationBackend
from .media import OpusPacketPacer, StreamingOpusToPcm, StreamingPcmToOpus
from .playback import PlaybackReadyTimeout
from .providers import RealtimeProviderError, RealtimeProviderTimeout

logger = logging.getLogger(__name__)
telemetry_logger = logging.getLogger("uvicorn.error")


class SpeechToSpeechInput:
    """Bridge one device input to one provider session; never switch providers on failure."""

    def __init__(
        self,
        backend: ConversationBackend,
        decoder: StreamingOpusToPcm,
        turn_id: str,
        input_accepted: asyncio.Event | None = None,
    ):
        self.backend = backend
        self.decoder = decoder
        self.turn_id = turn_id
        self.generation = backend.begin_turn(turn_id)
        self.input_accepted = input_accepted or asyncio.Event()
        self._upload_task = asyncio.create_task(self._upload())
        self._upload_error: Exception | None = None
        self._ended = False
        self._closed = False
        self._opus_packets = 0
        self._opus_bytes = 0
        self._pcm_bytes = 0
        self._started_at = time.monotonic()
        self._first_packet_at: float | None = None
        self._send_seconds = 0.0
        self._pacing_seconds = 0.0

    @property
    def endpoint_event(self) -> asyncio.Event:
        return self.backend.endpoint_event

    def endpoint_detected(self) -> bool:
        return self.backend.endpoint_detected() or self._upload_error is not None

    async def _upload(self) -> None:
        target = time.monotonic()
        try:
            async for pcm in self.decoder.chunks():
                if not self._pcm_bytes:
                    telemetry_logger.info(
                        "voice input turn=%s first_pcm_ready elapsed_ms=%d",
                        self.turn_id,
                        round((time.monotonic() - self._started_at) * 1000),
                    )
                self._pcm_bytes += len(pcm)
                now = time.monotonic()
                if target > now:
                    await asyncio.sleep(target - now)
                    self._pacing_seconds += time.monotonic() - now
                send_started = time.monotonic()
                await self.backend.send_audio(pcm, generation=self.generation)
                self._send_seconds += time.monotonic() - send_started
                # Keep the audio clock independent of send/scheduler overhead.
                # Catch up at most 100 ms after a stall, never burst an entire turn.
                target = max(target + len(pcm) / 32000, time.monotonic() - 0.1)
        except Exception as exc:
            self._upload_error = exc
            telemetry_logger.warning(
                "voice input turn=%s upload_error=%s", self.turn_id, type(exc).__name__
            )
            self.backend.endpoint_event.set()

    async def send_audio(self, packet: bytes) -> None:
        if self._upload_error is not None:
            raise self._upload_error
        if self._closed or self._ended or self.backend.endpoint_detected():
            return
        self._opus_packets += 1
        self._opus_bytes += len(packet)
        if self._opus_packets == 1:
            self._first_packet_at = time.monotonic()
            telemetry_logger.info(
                "voice input turn=%s first_device_packet bytes=%d elapsed_ms=%d",
                self.turn_id,
                len(packet),
                round((self._first_packet_at - self._started_at) * 1000),
            )
        write = asyncio.create_task(self.decoder.write(packet))
        endpoint = asyncio.create_task(self.endpoint_event.wait())
        try:
            async with asyncio.timeout(self.backend.config.timeout_seconds):
                done, _ = await asyncio.wait(
                    {write, endpoint, self._upload_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            if self._upload_error is not None:
                raise self._upload_error
            if write in done:
                await write
            elif self._upload_task in done:
                await self._upload_task
                if not self._ended and not self.backend.endpoint_detected():
                    raise RealtimeProviderError("voice-route", "audio-upload-ended")
            # A cloud endpoint is normal. Return to the device receive loop so
            # it can process the transcript/error already waiting in events().
        except TimeoutError:
            raise RealtimeProviderTimeout("voice-route", "input-write") from None
        finally:
            for task in (write, endpoint):
                task.cancel()
            await asyncio.gather(write, endpoint, return_exceptions=True)

    def invalidate(self) -> None:
        self.backend.invalidate(generation=self.generation)
        self._upload_task.cancel()

    async def finish_input(self) -> None:
        if self._ended:
            return
        self._ended = True
        ended_at = time.monotonic()
        telemetry_logger.info(
            "voice input turn=%s receive_span_ms=%d audio_ms=%d",
            self.turn_id,
            round((ended_at - self._first_packet_at) * 1000)
            if self._first_packet_at is not None
            else 0,
            self._opus_packets * 60,
        )
        telemetry_logger.info(
            "voice input turn=%s device_input_ended opus_packets=%d opus_bytes=%d pcm_bytes=%d",
            self.turn_id,
            self._opus_packets,
            self._opus_bytes,
            self._pcm_bytes,
        )
        async with asyncio.timeout(self.backend.config.timeout_seconds):
            await self.decoder.finish()
            await self._upload_task
            if self._upload_error is not None:
                raise self._upload_error
            await self.backend.end_input(generation=self.generation)
            telemetry_logger.info(
                "voice input turn=%s upstream_input_ended drain_ms=%d pcm_bytes=%d",
                self.turn_id,
                round((time.monotonic() - ended_at) * 1000),
                self._pcm_bytes,
            )
            telemetry_logger.info(
                "voice input turn=%s upload_send_ms=%d upload_pacing_ms=%d",
                self.turn_id,
                round(self._send_seconds * 1000),
                round(self._pacing_seconds * 1000),
            )

    async def cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        telemetry_logger.info(
            "voice input turn=%s closed opus_packets=%d opus_bytes=%d pcm_bytes=%d input_ended=%s",
            self.turn_id,
            self._opus_packets,
            self._opus_bytes,
            self._pcm_bytes,
            self._ended,
        )
        self.invalidate()
        try:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2):
                    await self.backend.cancel(generation=self.generation)
            await self.backend.close()
        finally:
            await asyncio.gather(self._upload_task, return_exceptions=True)
            await self.decoder.cancel()


async def record_s2s_usage(
    session_factory,
    *,
    conversation_id: str,
    user_id: str,
    device_id: str,
    model: str,
    provider_session_id: str,
    response_id: str,
    turn_id: str,
    usage: dict[str, object],
    completed: bool,
    latency_ms: int | None,
    error_code: str | None,
    provider: str = "doubao",
    operation: str = "realtime_s2s",
) -> None:
    """Unknown supplier cost is nullable, never an invented zero-cost ASR/LLM/TTS bill."""
    identity = f"{provider_session_id}:{response_id or turn_id}"
    event_key = provider + ":" + hashlib.sha256(identity.encode()).hexdigest()
    details = ProviderUsageDetails(provider_usage=usage).model_dump_json()
    async with session_factory() as session:
        if await session.scalar(
            select(ProviderUsage.id).where(ProviderUsage.billing_event_key == event_key)
        ):
            return
        conversation = await session.get(ConversationSession, conversation_id)
        if conversation is None:
            return
        session.add(
            ProviderUsage(
                session_id=conversation_id,
                user_id=user_id,
                device_id=device_id,
                provider=provider,
                model=model,
                operation=operation,
                billing_event_key=event_key,
                provider_request_id=(response_id or provider_session_id)[:160],
                usage_details_json=details,
                pricing_version=None,
                cost_micros=None,
                cost_status="unknown",
                latency_ms=latency_ms or 0,
                error_code=error_code,
            )
        )
        if completed:
            conversation.turn_count += 1
            if conversation.first_audio_latency_ms is None:
                conversation.first_audio_latency_ms = latency_ms
            session.add(
                UsageEvent(
                    user_id=user_id,
                    device_id=device_id,
                    kind="voice-turn",
                    quantity=1,
                    # Known subtotal only; the ProviderUsage row is unknown.
                    provider_cost_micros=0,
                )
            )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            if not await session.scalar(
                select(ProviderUsage.id).where(ProviderUsage.billing_event_key == event_key)
            ):
                raise


async def process_s2s_turn(
    websocket,
    lease,
    source: SpeechToSpeechInput,
    *,
    device_id: str,
    user_id: str,
    conversation_id: str,
    snapshot,
    history,
    timeline,
    playback,
    user_exit_event,
    authorize: Callable[[], Awaitable[None]],
    telemetry_tasks: set[asyncio.Task],
) -> bool:
    settings = websocket.app.state.settings
    manager = websocket.app.state.device_connections
    turn_id = source.turn_id
    reply_id = None
    encoder = None
    packet_task = None
    finish_task = None
    transcript = ""
    reply = ""
    transcript_ready = False
    pending_audio: list[bytes] = []
    pending_audio_bytes = 0
    audio_bytes = 0
    usage: dict[str, object] = {}
    response_id = ""
    completed = False
    provider_done = False
    stopped = False
    error_code: str | None = "s2s-incomplete"

    async def send(payload):
        if not await manager.send_json_for_lease(lease, {"turn_id": turn_id, **payload}):
            raise ConnectionError("device lease expired")

    async def send_packet(packet):
        delivered = await manager.send_bytes_for_lease(lease, packet)
        if delivered:
            timeline.mark("gateway_first_packet")
        return delivered

    pacer = OpusPacketPacer(send_packet, startup_burst_packets=5)

    async def pump_packets():
        async for packet in encoder.packets(prebuffer_packets=5):
            if not await pacer.send(packet):
                raise ConnectionError("device lease expired")

    async def play(pcm):
        nonlocal encoder, packet_task, reply_id, audio_bytes
        if encoder is None:
            await authorize()
            timeline.mark("tts_first_pcm")
            reply_id = await playback.initiate(websocket, lease, turn_id)
            timeline.bind_reply(reply_id)
            await playback.ensure_ready(websocket, lease, reply_id, turn_id)
            timeline.mark("device_playback_ready")
            encoder = StreamingPcmToOpus(settings.ffmpeg_path)
            await encoder.start()
            packet_task = asyncio.create_task(pump_packets())
            await send({"type": "llm", "emotion": "neutral"})
        if packet_task.done():
            await packet_task
        await encoder.write(pcm)
        audio_bytes += len(pcm)

    try:
        await authorize()
        # Finish input concurrently: the reader must keep consuming response/usage
        # even while the provider acknowledges the local input commit.
        finish_task = asyncio.create_task(source.finish_input())
        async with asyncio.timeout(120):
            async for event in source.backend.events():
                if finish_task.done():
                    await finish_task
                if event.generation != source.generation or event.turn_id != turn_id:
                    continue
                if event.response_id:
                    response_id = event.response_id
                if event.type == "transcript_final":
                    transcript = event.text.strip()
                    timeline.mark("asr_transcription_completed")
                    safety = evaluate_text(transcript)
                    if safety.end_session:
                        error_code = "user-exit"
                        await send({"type": "listen", "state": "standby", "reason": error_code})
                        user_exit_event.set()
                        return True
                    if safety.fixed_response:
                        error_code = "safety-blocked"
                        await send(
                            {
                                "type": "alert",
                                "status": safety.category,
                                "message": safety.fixed_response,
                            }
                        )
                        return False
                    if not transcript:
                        error_code = "asr-no-speech"
                        await send({"type": "listen", "state": "resume"})
                        return False
                    transcript_ready = True
                    source.input_accepted.set()
                    await send({"type": "stt", "text": transcript, "emotion": "neutral"})
                    await send({"type": "llm", "emotion": "thinking"})
                    for pcm in pending_audio:
                        await play(pcm)
                    pending_audio.clear()
                    pending_audio_bytes = 0
                elif event.type in {"text_delta", "text_final"}:
                    reply = reply + event.text if event.type == "text_delta" else event.text
                    if len(reply) > 8000:
                        raise RealtimeProviderError("voice-route", "reply-too-large")
                    safety = evaluate_text(reply)
                    if safety.fixed_response:
                        error_code = "safety-blocked"
                        await send(
                            {
                                "type": "alert",
                                "status": safety.category,
                                "message": safety.fixed_response,
                            }
                        )
                        return False
                    if event.type == "text_final" and reply:
                        await send({"type": "tts", "state": "sentence_start", "text": reply})
                elif event.type == "audio":
                    if transcript_ready:
                        await play(event.audio)
                    else:
                        pending_audio_bytes += len(event.audio)
                        if pending_audio_bytes > 192000:
                            raise RealtimeProviderError("voice-route", "transcript-order-timeout")
                        pending_audio.append(event.audio)
                elif event.type == "usage":
                    usage = event.usage or {}
                elif event.type == "done":
                    provider_done = True
                    break
            await finish_task
            if not provider_done or not transcript_ready or not audio_bytes:
                raise RealtimeProviderError("voice-route", "empty-response")
            await encoder.finish()
            await packet_task
            drained = await playback.stop(
                websocket,
                lease,
                reply_id,
                turn_id,
                wait_for_drain=True,
            )
            stopped = True
            if not drained:
                raise RealtimeProviderError("voice-route", "tts-drained-timeout")
            if playback.last_drain_acknowledged:
                await source.backend.playback_completed()
            if transcript and reply:
                history.extend(
                    [
                        {"role": "user", "content": transcript},
                        {"role": "assistant", "content": reply},
                    ]
                )
                del history[:-20]
            completed = True
            error_code = None
            timeline.mark("turn_completed")
            if not playback.strict_ack and not playback.last_drain_acknowledged:
                await send({"type": "turn", "state": "completed", "reply_id": reply_id})
            return False
    except asyncio.CancelledError:
        error_code = "turn-cancelled"
        raise
    except Exception as exc:
        error_code = getattr(exc, "code", "s2s-unavailable")
        with contextlib.suppress(Exception):
            await send(
                {
                    "type": "error",
                    "code": error_code,
                    "message": "当前语音方案暂时不可用，请重试或手动更换语音方案。",
                }
            )
        if isinstance(exc, PlaybackReadyTimeout):
            await manager.retire(lease, code=1011, reason="tts ready timeout")
        return False
    finally:
        source.invalidate()
        for task in (finish_task, packet_task):
            if task is not None:
                task.cancel()
        if reply_id is not None and not stopped and playback.reply_id == reply_id:
            with contextlib.suppress(Exception):
                await playback.stop(websocket, lease, reply_id, turn_id, wait_for_drain=False)
        await asyncio.gather(*(t for t in (finish_task, packet_task) if t), return_exceptions=True)
        try:
            await source.cancel()
        finally:
            if encoder is not None:
                await encoder.cancel()
        task = asyncio.create_task(
            record_s2s_usage(
                websocket.app.state.session_factory,
                conversation_id=conversation_id,
                user_id=user_id,
                device_id=device_id,
                model=snapshot.realtime_model,
                provider_session_id=source.backend.session_id,
                response_id=response_id,
                turn_id=turn_id,
                usage=usage,
                completed=completed,
                provider=snapshot.realtime_provider,
                operation="managed_dialog"
                if snapshot.route_kind == "managed_app"
                else "realtime_s2s",
                latency_ms=timeline.elapsed_ms("gateway_first_packet"),
                error_code=error_code,
            )
        )
        telemetry_tasks.add(task)

        def observe_usage(finished):
            telemetry_tasks.discard(finished)
            try:
                finished.result()
            except Exception:
                logger.error("S2S usage recording failed turn_id=%s", turn_id)

        task.add_done_callback(observe_usage)
        telemetry_logger.info(
            "voice turn outcome %s",
            json.dumps(
                timeline.as_record(
                    serial=lease.serial_number,
                    conversation_id=conversation_id,
                    outcome="completed" if completed else "failed",
                    error_code=error_code,
                    fallback_operations=set(),
                ),
                separators=(",", ":"),
            ),
        )
