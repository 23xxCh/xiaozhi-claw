import asyncio
import base64
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from backend.ai.context import ContextBuilder, LlmRequest
from backend.app.audio_formats import ogg_opus_packets
from backend.app.config import Settings
from backend.realtime import providers as realtime_providers
from backend.realtime.providers import (
    DeepSeekStreamingLlmProvider,
    QwenRealtimeAsrSession,
    QwenRealtimeTtsSession,
    RealtimeProviderBundle,
    RealtimeProviderError,
    RealtimeProviderTimeout,
)
from backend.realtime.reply_policy import build_voice_reply_policy


from backend.realtime.session import (
    SentenceBuffer,
    is_non_speech_filler,
    sanitize_spoken_text,
)


def test_news_reply_has_room_for_three_complete_items():
    policy = build_voice_reply_policy("播报三条科技新闻")
    assert policy.max_spoken_chars == 240
    assert "最多三条" in policy.context
    assert build_voice_reply_policy("你好").max_spoken_chars == 60


@pytest.mark.asyncio
async def test_deepseek_streaming_request_uses_fast_non_thinking_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n',
        )

    def client_factory(*args, **kwargs) -> httpx.AsyncClient:
        del args, kwargs
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(realtime_providers.httpx, "AsyncClient", client_factory)
    settings = Settings(
        provider_mode="custom",
        llm_url="https://llm.example/v1",
        llm_api_key="secret",
        llm_model="deepseek-v4-flash",
    )
    provider = DeepSeekStreamingLlmProvider(settings)

    chunks = [
        chunk
        async for chunk in provider.reply_stream(
            LlmRequest(
                context=ContextBuilder().build(
                    system_prompt="你是助手",
                    current_question="你好",
                    history=[],
                    memories=[],
                    summaries=[],
                    tools=None,
                ),
                model="deepseek-v4-flash",
                temperature=0.35,
            )
        )
    ]

    assert chunks == ["好"]
    assert captured["temperature"] == 0.35
    assert captured["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_deepseek_provider_reuses_one_client_and_closes_it_once() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n',
        )

    class _CountingClient(httpx.AsyncClient):
        close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            await super().aclose()

    client = _CountingClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        provider_mode="custom",
        llm_url="https://llm.example/v1",
        llm_api_key="secret",
        llm_model="deepseek-v4-flash",
    )
    provider = DeepSeekStreamingLlmProvider(settings, client=client)
    request = LlmRequest(
        context=ContextBuilder().build(
            system_prompt="你是助手",
            current_question="你好",
            history=[],
            memories=[],
            summaries=[],
            tools=None,
        ),
        model="deepseek-v4-flash",
        temperature=0.35,
    )

    for _ in range(2):
        assert [chunk async for chunk in provider.reply_stream(request)] == ["好"]

    assert requests == 2
    assert client.is_closed is False
    await provider.aclose()
    await provider.aclose()
    assert client.close_calls == 1
    assert client.is_closed is True


class _FakeRealtimeSocket:
    def __init__(self, events: list[dict[str, object]] | None = None) -> None:
        self.sent: list[str] = []
        self.events = list(events or [{"type": "session.updated"}])

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        return json.dumps(self.events.pop(0))

    async def close(self) -> None:
        return None


class _PlaybackSensitiveRealtimeSocket(_FakeRealtimeSocket):
    """Simulate a provider whose final event must be drained promptly."""

    def __init__(self) -> None:
        super().__init__(
            [
                {"type": "response.audio.delta", "delta": base64.b64encode(b"pcm").decode()},
                {"type": "response.done"},
            ]
        )
        self.first_audio_read_at: float | None = None

    async def recv(self) -> str:
        if self.first_audio_read_at is not None:
            if asyncio.get_running_loop().time() - self.first_audio_read_at > 0.01:
                raise TimeoutError("provider final event was not drained while playback was slow")
        payload = await super().recv()
        if self.first_audio_read_at is None:
            self.first_audio_read_at = asyncio.get_running_loop().time()
        return payload


class _SlowCloseRealtimeSocket(_FakeRealtimeSocket):
    def __init__(self, events: list[dict[str, object]]) -> None:
        super().__init__(events)
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> None:
        self.close_started.set()
        await self.release_close.wait()


@pytest.mark.asyncio
async def test_qwen_realtime_tts_session_includes_selected_speech_rate(monkeypatch) -> None:
    socket = _FakeRealtimeSocket()
    connect_kwargs: dict[str, object] = {}

    async def fake_connect(*args, **kwargs):
        del args
        connect_kwargs.update(kwargs)
        return socket

    monkeypatch.setattr(realtime_providers, "connect", fake_connect)
    settings = Settings(
        provider_mode="custom",
        qwen_realtime_tts_url="wss://tts.example/realtime",
        qwen_realtime_tts_model="qwen3-tts-flash-realtime",
        tts_api_key="secret",
    )

    await QwenRealtimeTtsSession.open(settings, voice="Cherry", speech_rate=1.2)

    update = json.loads(socket.sent[0])
    assert update["type"] == "session.update"
    assert update["session"]["voice"] == "Cherry"
    assert update["session"]["speech_rate"] == 1.2
    assert connect_kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_qwen_tts_provider_timeout_does_not_count_slow_audio_consumer() -> None:
    socket = _FakeRealtimeSocket(
        [
            {"type": "response.audio.delta", "delta": base64.b64encode(b"pcm").decode()},
            {"type": "response.done"},
        ]
    )
    session = QwenRealtimeTtsSession(socket, event_timeout_seconds=0.01)

    chunks: list[bytes] = []
    async for chunk in session.synthesize("较长回复"):
        chunks.append(chunk)
        await asyncio.sleep(0.02)

    assert chunks == [b"pcm"]


@pytest.mark.asyncio
async def test_qwen_tts_drains_provider_while_playback_consumer_is_slow() -> None:
    socket = _PlaybackSensitiveRealtimeSocket()
    session = QwenRealtimeTtsSession(socket, event_timeout_seconds=0.1)

    chunks: list[bytes] = []
    async for chunk in session.synthesize("较长回复"):
        chunks.append(chunk)
        await asyncio.sleep(0.02)

    assert chunks == [b"pcm"]


@pytest.mark.asyncio
async def test_qwen_tts_wraps_provider_timeout_with_stable_stage() -> None:
    class _TimeoutSocket(_FakeRealtimeSocket):
        async def recv(self) -> str:
            raise TimeoutError

    session = QwenRealtimeTtsSession(_TimeoutSocket(), event_timeout_seconds=0.01)

    with pytest.raises(RealtimeProviderTimeout) as raised:
        async for _ in session.synthesize("超时测试"):
            pass

    assert raised.value.provider == "qwen-tts"
    assert raised.value.stage == "response-event"
    assert str(raised.value) == "qwen-tts realtime provider timeout: response-event"


@pytest.mark.asyncio
async def test_qwen_tts_rejects_completed_response_without_audio() -> None:
    session = QwenRealtimeTtsSession(
        _FakeRealtimeSocket([{"type": "response.done"}]),
        event_timeout_seconds=0.1,
    )

    with pytest.raises(RealtimeProviderError, match="empty-audio"):
        async for _ in session.synthesize("没有音频"):
            pass


def test_sentence_buffer_prefers_natural_clause_over_mid_sentence_split() -> None:
    buffer = SentenceBuffer()

    assert buffer.feed("很抱歉，我无法直接获取实时时间，建议您查看") == [
        "很抱歉，我无法直接获取实时时间，"
    ]
    assert buffer.flush() == "建议您查看"


def test_sentence_buffer_starts_unpunctuated_reply_without_waiting_for_full_sentence() -> None:
    buffer = SentenceBuffer()

    assert buffer.feed("短" * 7) == []
    assert buffer.feed("句") == ["短" * 7 + "句"]
    assert buffer.feed("后续内容") == []
    assert buffer.flush() == "后续内容"


def test_sentence_buffer_keeps_hard_limit_after_first_chunk() -> None:
    buffer = SentenceBuffer()

    assert buffer.feed("短" * 8) == ["短" * 8]
    assert buffer.feed("句" * 32) == ["句" * 32]


def test_story_request_gets_a_larger_spoken_budget_and_explicit_capability() -> None:
    policy = build_voice_reply_policy(
        "给我讲一个有结尾的短故事",
        now=datetime(2026, 8, 28, 15, 30, tzinfo=UTC),
    )

    assert policy.max_spoken_chars == 160
    assert policy.max_spoken_segments == 5
    assert "可以讲原创短故事" in policy.context
    assert "直接开始讲故事" in policy.context


def test_every_voice_turn_gets_an_authoritative_local_date() -> None:
    policy = build_voice_reply_policy(
        "今天几号",
        now=datetime(2026, 8, 28, 15, 30, tzinfo=UTC),
    )

    assert policy.max_spoken_chars == 60
    assert policy.max_spoken_segments == 2
    assert "2026年8月28日" in policy.context
    assert "星期五" in policy.context
    assert "香港时间" in policy.context


def test_sanitize_spoken_text_removes_non_speech_markup() -> None:
    assert (
        sanitize_spoken_text(
            "[[face:happy]]**好的**，[mood:happy]（轻轻点头）请看 https://example.com/path"
        )
        == "好的，请看"
    )


@pytest.mark.parametrize("text", ["嗯", "嗯。", " 嗯嗯 ", "呃……", "哦！", "嗯啊呃"])
def test_short_asr_fillers_are_treated_as_non_speech(text: str) -> None:
    assert is_non_speech_filler(text) is True


@pytest.mark.parametrize("text", ["好", "停", "几点", "嗯好的", "啊为什么", "哦我知道了"])
def test_meaningful_short_transcripts_are_not_treated_as_non_speech(text: str) -> None:
    assert is_non_speech_filler(text) is False


@pytest.mark.asyncio
async def test_qwen_realtime_asr_wraps_raw_opus_and_uses_manual_turn_detection(
    monkeypatch,
) -> None:
    socket = _FakeRealtimeSocket(
        [
            {"type": "session.updated"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "你好，小智",
                "emotion": "happy",
            },
            {"type": "session.finished"},
        ]
    )
    connect_kwargs: dict[str, object] = {}

    async def fake_connect(*args, **kwargs):
        del args
        connect_kwargs.update(kwargs)
        return socket

    monkeypatch.setattr(realtime_providers, "connect", fake_connect)
    settings = Settings(
        provider_mode="custom",
        qwen_realtime_asr_url="wss://asr.example/realtime",
        qwen_realtime_asr_model="qwen3-asr-flash-realtime",
        asr_api_key="secret",
    )

    session = await QwenRealtimeAsrSession.open(settings)
    await session.send_audio(b"raw-opus-one")
    await session.send_audio(b"raw-opus-two")
    result = await session.finish()

    messages = [json.loads(payload) for payload in socket.sent]
    assert messages[0]["session"]["turn_detection"] is None
    append_messages = [item for item in messages if item["type"] == "input_audio_buffer.append"]
    wrapped = b"".join(base64.b64decode(item["audio"]) for item in append_messages)
    assert ogg_opus_packets(wrapped) == [b"raw-opus-one", b"raw-opus-two"]
    assert session.endpoint_detected() is False
    assert [item["type"] for item in messages[-2:]] == [
        "input_audio_buffer.commit",
        "session.finish",
    ]
    assert result.text == "你好，小智"
    assert result.emotion == "happy"
    assert result.transcription_completed_at is not None
    assert result.session_finished_at is not None
    assert result.transcription_completed_at <= result.session_finished_at
    assert connect_kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_qwen_realtime_asr_returns_before_close_handshake_finishes() -> None:
    socket = _SlowCloseRealtimeSocket(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "关闭握手不应阻塞回复",
            },
            {"type": "session.finished"},
        ]
    )
    session = QwenRealtimeAsrSession(socket)
    session._reader_task = asyncio.create_task(session._read_events())
    finish_task = asyncio.create_task(session.finish())

    await socket.close_started.wait()
    await asyncio.sleep(0)
    try:
        assert finish_task.done()
        assert (await finish_task).text == "关闭握手不应阻塞回复"
    finally:
        socket.release_close.set()
        if not finish_task.done():
            await finish_task
        if session._close_task is not None:
            await session._close_task


@pytest.mark.asyncio
async def test_qwen_realtime_error_uses_stable_code_without_transcript(monkeypatch) -> None:
    socket = _FakeRealtimeSocket(
        [
            {"type": "session.updated"},
            {
                "type": "invalid_request_error",
                "error": {"type": "invalid_audio", "message": "private audio detail"},
            },
        ]
    )

    async def fake_connect(*args, **kwargs):
        del args, kwargs
        return socket

    monkeypatch.setattr(realtime_providers, "connect", fake_connect)
    settings = Settings(
        provider_mode="custom",
        qwen_realtime_asr_url="wss://asr.example/realtime",
        asr_api_key="secret",
    )
    session = await QwenRealtimeAsrSession.open(settings)
    await session.send_audio(b"raw-opus")

    with pytest.raises(RealtimeProviderError, match="invalid_audio") as error:
        await session.finish()
    assert "private audio detail" not in str(error.value)


@pytest.mark.asyncio
async def test_qwen_local_stop_commits_before_finishing_manual_session() -> None:
    socket = _FakeRealtimeSocket(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "本地提前停止",
            },
            {"type": "session.finished"},
        ]
    )
    session = QwenRealtimeAsrSession(socket)
    session._reader_task = asyncio.create_task(session._read_events())

    result = await session.finish()

    messages = [json.loads(payload) for payload in socket.sent]
    assert [item["type"] for item in messages] == [
        "input_audio_buffer.commit",
        "session.finish",
    ]
    assert result.text == "本地提前停止"


def _selected_models(**updates):
    return {
        "asr_provider": "dashscope",
        "asr_model": "selected-asr&revision=2",
        "llm_provider": "deepseek",
        "llm_model": "selected-llm",
        "tts_provider": "dashscope",
        "tts_model": "selected-tts&revision=3",
        **updates,
    }


@pytest.mark.asyncio
async def test_bound_bundle_uses_selected_asr_tts_models_and_preserves_source(monkeypatch):
    urls = []

    class BindingSocket:
        def __init__(self):
            self.events = asyncio.Queue()

        async def send(self, value):
            if json.loads(value)["type"] == "session.update":
                self.events.put_nowait(json.dumps({"type": "session.updated"}))

        async def recv(self):
            return await self.events.get()

        async def close(self):
            return None

    async def fake_connect(url, **kwargs):
        urls.append(url)
        return BindingSocket()

    monkeypatch.setattr(realtime_providers, "connect", fake_connect)
    settings = Settings(provider_mode="custom", asr_model="source-asr", tts_model="source-tts")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    source = RealtimeProviderBundle(settings, DeepSeekStreamingLlmProvider(settings, client=client))
    bound = source.for_models(**_selected_models())
    asr = tts = None
    try:
        asr = await bound.open_asr()
        tts = await bound.open_tts("Cherry")
        assert [parse_qs(urlsplit(url).query) for url in urls] == [
            {"model": ["selected-asr&revision=2"]},
            {"model": ["selected-tts&revision=3"]},
        ]
        assert bound.settings.asr_model == "selected-asr&revision=2"
        assert bound.settings.tts_model == "selected-tts&revision=3"
        assert bound.settings.llm_model == "selected-llm"
        assert source.settings.asr_model == "source-asr"
        assert source.settings.tts_model == "source-tts"
        source.settings.llm_url = "https://changed.example"
        assert bound.settings.llm_url != source.settings.llm_url
        await bound.aclose()
        assert client.is_closed is False
    finally:
        if asr:
            await asr.cancel()
        if tts:
            await tts.cancel()
        await source.aclose()
    assert client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["deepseek", "openai-compatible"])
async def test_binding_shares_client_but_isolates_provider_specific_llm_options(provider):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, text='data: {"choices":[{"delta":{"content":"好"}}]}\n\ndata: [DONE]\n\n'
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(provider_mode="custom", llm_url="https://llm.example/v1")
    source = RealtimeProviderBundle(settings, DeepSeekStreamingLlmProvider(settings, client=client))
    bound = source.for_models(**_selected_models(llm_provider=provider))
    try:
        assert bound.llm._client is source.llm._client is client
        request = LlmRequest(
            context=ContextBuilder().build(
                system_prompt="你是助手",
                current_question="你好",
                history=[],
                memories=[],
                summaries=[],
                tools=None,
            ),
            model=bound.settings.llm_model,
            temperature=0.35,
        )
        assert [part async for part in bound.llm.reply_stream(request)] == ["好"]
        assert requests[0]["model"] == "selected-llm"
        assert ("thinking" in requests[0]) is (provider == "deepseek")
        await bound.llm.aclose()
        await bound.aclose()
        assert client.is_closed is False
    finally:
        await source.aclose()
    assert client.is_closed


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"asr_provider": "other"}, "unsupported-asr-provider"),
        ({"tts_provider": "other"}, "unsupported-tts-provider"),
        ({"llm_provider": "other"}, "unsupported-llm-provider"),
        ({"asr_model": ""}, "missing-model"),
    ],
)
def test_binding_rejects_unsupported_routes_even_in_mock_mode(updates, code):
    source = realtime_providers.create_realtime_providers(Settings(provider_mode="mock"))
    with pytest.raises(RealtimeProviderError, match=code):
        source.for_models(**_selected_models(**updates))


@pytest.mark.asyncio
async def test_valid_binding_keeps_mock_implementations():
    source = realtime_providers.create_realtime_providers(Settings(provider_mode="mock"))
    bound = source.for_models(**_selected_models())
    assert bound.mock is True
    assert bound.llm is source.llm
    assert isinstance(await bound.open_asr(), realtime_providers.MockAsrSession)
    assert isinstance(await bound.open_tts("Cherry"), realtime_providers.MockTtsSession)
