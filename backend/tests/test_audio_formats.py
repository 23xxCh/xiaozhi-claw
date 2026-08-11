import io
import math
import shutil
import struct
import wave

import pytest

from backend.app.audio_formats import (
    FfmpegOpusNormalizer,
    ogg_opus_packets,
    opus_packets_to_ogg,
)


def test_opus_packets_round_trip_through_ogg_container() -> None:
    packets = [b"opus-frame-one", b"opus-frame-two", b"opus-frame-three"]

    ogg = opus_packets_to_ogg(packets, input_sample_rate=16000, frame_duration_ms=60)

    assert ogg.startswith(b"OggS")
    assert ogg_opus_packets(ogg) == packets


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
async def test_ffmpeg_normalizer_produces_playable_opus_packets() -> None:
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        samples = [
            int(6000 * math.sin(2 * math.pi * 440 * index / 24000))
            for index in range(7200)
        ]
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))

    packets = await FfmpegOpusNormalizer("ffmpeg").normalize_tts(wav_buffer.getvalue())

    assert len(packets) >= 4
    assert all(packets)
