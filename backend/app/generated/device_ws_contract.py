# ruff: noqa: E501
"""Generated device WSS V1 vocabulary. Do not edit by hand."""

DEVICE_WS_PROTOCOL_VERSION = 1
DEVICE_WS_MESSAGE_TYPES = frozenset(("hello", "listen", "tts", "turn", "mcp", "system", "alert", "device_stage", "device_config_ack",))
TTS_STATES = frozenset(("start", "ready", "stop", "drained",))
DEVICE_STAGE_STATES = frozenset(("capture_started", "speaker_pcm_started", "playback_drained",))
OPTIONAL_CORRELATION_FIELDS = frozenset(("session_id", "turn_id", "reply_id",))
