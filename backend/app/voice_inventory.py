"""Reviewed vendor snapshot; compatibility does not imply release approval."""

import json
from pathlib import Path

VOICE_CATALOG = json.loads(
    Path(__file__).with_name("voice_catalog.json").read_text(encoding="utf-8")
)
QWEN_VOICE_MODELS = {v["voice"]: frozenset(v["models"]) for v in VOICE_CATALOG["qwen"]}
VOLC_TTS_VOICES = frozenset(v["voice"] for v in VOICE_CATALOG["volc"])
VOLC_S2S_VOICES = frozenset(v["voice"] for v in VOICE_CATALOG["volc"] if v["s2s"])
