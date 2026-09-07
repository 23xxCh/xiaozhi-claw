"""No paid calls: exercise the real gateway/codecs against a fake Doubao socket."""

import json
import math
import shutil
import struct
import subprocess
import sys
import wave

import psutil
import pytest

from scripts import doubao_gateway_probe as probe


def write_wav(path, frames=9600):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(
            b"".join(
                struct.pack("<h", round(5000 * math.sin(index * 2 * math.pi * 400 / 16000)))
                for index in range(frames)
            )
        )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_offline_probe_uses_real_gateway_codecs_and_isolated_database(
    tmp_path, monkeypatch, capsys
):
    audio = tmp_path / "input.wav"
    write_wav(audio)
    unrelated = tmp_path / "unrelated.db"
    unrelated.write_bytes(b"do-not-modify-real-database")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{unrelated.as_posix()}")
    monkeypatch.setenv("DOUBAO_API_KEY", "must-not-be-disclosed")
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    output = tmp_path / "run" / "doubao" / "offline.json"
    before = {child.pid for child in psutil.Process().children(recursive=True)}
    temporary_directories = []
    original_temporary_directory = probe.tempfile.TemporaryDirectory

    def temporary_directory(**kwargs):
        directory = original_temporary_directory(dir=tmp_path, **kwargs)
        temporary_directories.append(directory.name)
        return directory

    monkeypatch.setattr(probe.tempfile, "TemporaryDirectory", temporary_directory)

    assert probe.main(["--input-wav", str(audio), "--offline", "--output", str(output)]) == 0

    result = json.loads(output.read_text())
    assert result["synthetic_device"] and not result["physical_audio_verified"]
    assert result["external_provider_requested"] is False
    assert result["external_provider_called"] is False and result["provider_open_attempts"] == 1
    assert result["completed_turns"] == 1
    assert result["ready_ack"] and result["drained_ack"] and result["simulated_speaker_ack_sent"]
    assert result["offline_supplier_received_pcm_bytes"] == result["input_opus_packets"] * 1920
    assert result["offline_pcm_matches_raw_reference"]
    assert result["offline_wav_to_supplier_correlation"] > 0.9
    assert result["offline_append_count"] == result["input_opus_packets"] * 3
    assert result["offline_append_bytes_min"] == result["offline_append_bytes_max"] == 640
    assert result["offline_commit_count"] == 1 and result["offline_commit_after_last_append"]
    assert result["output_decoded_pcm_bytes"] == result["output_opus_packets"] * 2880
    assert result["provider_usage_recorded"] and result["provider_cost_status"] == "unknown"
    assert unrelated.read_bytes() == b"do-not-modify-real-database"
    assert not ({child.pid for child in psutil.Process().children(recursive=True)} - before)
    assert all(not probe.Path(directory).exists() for directory in temporary_directories)
    assert "must-not-be-disclosed" not in capsys.readouterr().out + output.read_text()


def test_input_over_fifteen_seconds_is_rejected_before_ffmpeg(tmp_path, monkeypatch):
    audio = tmp_path / "too-long.wav"
    write_wav(audio, 240001)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid input must not reach FFmpeg")

    monkeypatch.setattr(probe, "_ffmpeg", forbidden)
    with pytest.raises(probe.ProbeFailure, match="input-duration-must-be-within-15-seconds"):
        probe._input_packets(audio, "ffmpeg")


def test_watchdog_reaps_only_its_owned_worker(tmp_path, monkeypatch, capsys):
    original_popen = subprocess.Popen
    worker = []

    def sleeping_worker(_command, **kwargs):
        child = original_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        worker.append(child)
        return child

    monkeypatch.setattr(probe, "ROOT", tmp_path)
    monkeypatch.setattr(probe, "WORKER_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(probe.subprocess, "Popen", sleeping_worker)
    output = tmp_path / "run" / "doubao" / "timeout.json"
    before = {child.pid for child in psutil.Process().children(recursive=True)}
    assert (
        probe.main(
            ["--input-wav", str(tmp_path / "unused.wav"), "--offline", "--output", str(output)]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "gateway-probe-timeout"
    assert worker[0].poll() is not None
    assert not ({child.pid for child in psutil.Process().children(recursive=True)} - before)
