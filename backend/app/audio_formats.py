import asyncio
import struct

OGG_CRC_POLYNOMIAL = 0x04C11DB7


def _ogg_crc(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ OGG_CRC_POLYNOMIAL) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc


def _lacing_values(packet_size: int) -> bytes:
    if packet_size > 65024:
        raise ValueError("an Opus packet cannot exceed one Ogg page")
    full_segments, remainder = divmod(packet_size, 255)
    values = [255] * full_segments
    values.append(remainder)
    return bytes(values)


def _ogg_page(
    packet: bytes,
    *,
    header_type: int,
    granule_position: int,
    serial_number: int,
    sequence_number: int,
) -> bytes:
    lacing = _lacing_values(len(packet))
    header = bytearray(
        b"OggS"
        + bytes([0, header_type])
        + struct.pack("<QII", granule_position, serial_number, sequence_number)
        + b"\x00\x00\x00\x00"
        + bytes([len(lacing)])
        + lacing
    )
    page = header + packet
    page[22:26] = struct.pack("<I", _ogg_crc(page))
    return bytes(page)


def opus_packets_to_ogg(
    packets: list[bytes],
    *,
    input_sample_rate: int,
    frame_duration_ms: int,
) -> bytes:
    if not packets:
        raise ValueError("at least one Opus packet is required")
    if input_sample_rate <= 0 or frame_duration_ms <= 0:
        raise ValueError("sample rate and frame duration must be positive")

    serial_number = 0x48454E53
    opus_head = (
        b"OpusHead"
        + bytes([1, 1])
        + struct.pack("<H", 312)
        + struct.pack("<I", input_sample_rate)
        + struct.pack("<h", 0)
        + bytes([0])
    )
    vendor = b"Hensun Desk"
    opus_tags = b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", 0)

    pages = [
        _ogg_page(
            opus_head,
            header_type=0x02,
            granule_position=0,
            serial_number=serial_number,
            sequence_number=0,
        ),
        _ogg_page(
            opus_tags,
            header_type=0,
            granule_position=0,
            serial_number=serial_number,
            sequence_number=1,
        ),
    ]
    samples_per_packet = 48000 * frame_duration_ms // 1000
    granule_position = 0
    for index, packet in enumerate(packets):
        granule_position += samples_per_packet
        pages.append(
            _ogg_page(
                packet,
                header_type=0x04 if index == len(packets) - 1 else 0,
                granule_position=granule_position,
                serial_number=serial_number,
                sequence_number=index + 2,
            )
        )
    return b"".join(pages)


def ogg_opus_packets(data: bytes) -> list[bytes]:
    packets: list[bytes] = []
    pending = bytearray()
    offset = 0
    while offset < len(data):
        if data[offset : offset + 4] != b"OggS" or offset + 27 > len(data):
            raise ValueError("invalid Ogg page")
        segment_count = data[offset + 26]
        segment_table_end = offset + 27 + segment_count
        if segment_table_end > len(data):
            raise ValueError("truncated Ogg segment table")
        lacing = data[offset + 27 : segment_table_end]
        payload_end = segment_table_end + sum(lacing)
        if payload_end > len(data):
            raise ValueError("truncated Ogg payload")
        payload_offset = segment_table_end
        for segment_size in lacing:
            pending.extend(data[payload_offset : payload_offset + segment_size])
            payload_offset += segment_size
            if segment_size < 255:
                packet = bytes(pending)
                pending.clear()
                if not packet.startswith((b"OpusHead", b"OpusTags")):
                    packets.append(packet)
        offset = payload_end
    if pending:
        raise ValueError("unterminated Ogg packet")
    if not packets:
        raise ValueError("Ogg stream contains no Opus audio packets")
    return packets


class FfmpegOpusNormalizer:
    def __init__(self, ffmpeg_path: str) -> None:
        self.ffmpeg_path = ffmpeg_path

    async def normalize_tts(self, audio: bytes) -> list[bytes]:
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "libopus",
            "-b:a",
            "24k",
            "-frame_duration",
            "60",
            "-f",
            "ogg",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        output, error = await process.communicate(audio)
        if process.returncode != 0:
            detail = error.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FFmpeg could not normalize TTS audio: {detail}")
        return ogg_opus_packets(output)
