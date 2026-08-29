from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_device_wss_contract_is_generated_and_preserves_playback_handshake() -> None:
    source = ROOT / "contracts" / "device-ws-v1.json"
    contract = json.loads(source.read_text(encoding="utf-8"))

    assert contract["protocol_version"] == 1
    assert {"hello", "listen", "tts", "mcp", "system", "alert", "device_config_ack"} <= set(
        contract["message_types"]
    )
    assert set(contract["tts_states"]) == {"start", "ready", "stop", "drained"}
    assert set(contract["device_stage_states"]) == {
        "capture_started",
        "speaker_pcm_started",
        "playback_drained",
    }
    assert "turn_id" in contract["optional_correlation_fields"]

    result = subprocess.run(
        [sys.executable, "scripts/generate_device_contracts.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
