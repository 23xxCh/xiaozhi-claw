import asyncio
import os

import pytest

from backend.app.config import Settings
from backend.realtime.media import StreamingPcmToOpus
from backend.realtime.providers import QwenRealtimeAsrSession, QwenRealtimeTtsSession


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("HENSUN_RUN_QWEN_INTEGRATION") != "1",
    reason="set HENSUN_RUN_QWEN_INTEGRATION=1 to call the real Qwen services",
)
async def test_real_qwen_realtime_asr_does_not_require_batch_fallback() -> None:
    settings = Settings()
    tts = await QwenRealtimeTtsSession.open(settings, voice="Cherry", speech_rate=1.0)
    encoder = StreamingPcmToOpus(settings.ffmpeg_path)
    await encoder.start()

    async def collect_packets() -> list[bytes]:
        return [packet async for packet in encoder.packets()]

    packet_task = asyncio.create_task(collect_packets())
    async for pcm in tts.synthesize("你好，小智，这是实时语音识别测试。"):
        await encoder.write(pcm)
    await tts.finish()
    await encoder.finish()
    packets = await packet_task

    asr = await QwenRealtimeAsrSession.open(settings)
    for packet in packets:
        await asr.send_audio(packet)
    result = await asr.finish()

    assert "实时" in result.text
    assert "测试" in result.text
