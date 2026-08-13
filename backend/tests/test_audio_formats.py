import io
import math
import shutil
import struct
import wave

import pytest

from backend.app.audio_formats import (
    FfmpegOpusNormalizer,
    IncrementalOggOpusMuxer,
    _ogg_crc,
    ogg_opus_packets,
    opus_packets_to_ogg,
)


def test_opus_packets_round_trip_through_ogg_container() -> None:
    packets = [b"opus-frame-one", b"opus-frame-two", b"opus-frame-three"]

    ogg = opus_packets_to_ogg(packets, input_sample_rate=16000, frame_duration_ms=60)

    assert ogg.startswith(b"OggS")
    assert ogg_opus_packets(ogg) == packets


def test_incremental_ogg_muxer_preserves_sequence_crc_and_duration() -> None:
    muxer = IncrementalOggOpusMuxer(input_sample_rate=16000, frame_duration_ms=60)
    stream = muxer.add_packet(b"frame-one") + muxer.add_packet(b"frame-two")

    pages: list[bytes] = []
    offset = 0
    while offset < len(stream):
        segment_count = stream[offset + 26]
        lacing = stream[offset + 27 : offset + 27 + segment_count]
        page_size = 27 + segment_count + sum(lacing)
        pages.append(stream[offset : offset + page_size])
        offset += page_size

    assert [struct.unpack("<I", page[18:22])[0] for page in pages] == [0, 1, 2, 3]
    assert [struct.unpack("<Q", page[6:14])[0] for page in pages] == [0, 0, 2880, 5760]
    for page in pages:
        stored_crc = struct.unpack("<I", page[22:26])[0]
        without_crc = bytearray(page)
        without_crc[22:26] = b"\x00\x00\x00\x00"
        assert stored_crc == _ogg_crc(without_crc)
    assert ogg_opus_packets(stream) == [b"frame-one", b"frame-two"]


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
async def test_ffmpeg_normalizer_produces_playable_opus_packets() -> None:
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        samples = [int(6000 * math.sin(2 * math.pi * 440 * index / 24000)) for index in range(7200)]
        output.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))

    packets = await FfmpegOpusNormalizer("ffmpeg").normalize_tts(wav_buffer.getvalue())

    assert len(packets) >= 4
    assert all(packets)
