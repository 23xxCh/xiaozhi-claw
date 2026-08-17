# ruff: noqa: E501
"""Generated device WSS V1 vocabulary. Do not edit by hand."""

DEVICE_WS_PROTOCOL_VERSION = 1
DEVICE_WS_MESSAGE_TYPES = frozenset(("hello", "listen", "tts", "mcp", "system", "alert", "device_config_ack",))
TTS_STATES = frozenset(("start", "ready", "stop", "drained",))
OPTIONAL_CORRELATION_FIELDS = frozenset(("session_id", "turn_id", "reply_id",))
