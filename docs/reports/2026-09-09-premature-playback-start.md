# Premature device playback start

User reported that asking about Hubei returned the device to standby.
Gateway turn b553b1e8-55a2-4ae1-ae1c-6b081186c092 had ASR completion
at 312 ms, upstream TTS connected at 1013 ms, then cancellation without
LLM text or PCM. The device reconnected. The cancellation reason itself
was not captured on serial, so this does not prove which watchdog fired.

Code inspection found upstream TTS preconnection sent device tts.start
before any speakable text existed. Firmware arms a six-second first-PCM
watchdog on playback start. Slow LLM/search work therefore consumed a
playback deadline despite no synthesis having started.

Removed device initiation from upstream preconnection. Existing speak()
initiates playback when text is available. Upstream/encoder prewarming
and device-ready/synthesis overlap remain. No firmware flash needed.

Validation: gateway contract tests 26 passed, one unrelated OpenAPI snapshot
comparison failed (no control API changes in this patch); websocket,
first-audio fallback and face-control tests 30 passed. Tests now require
text before playback and no playback for empty/aborted pre-text replies.
Local gateway restarted and API/gateway readiness returned 200.

Physical retry and serial confirmation remain pending. Slow responses can
still reach the separate twelve-second reply deadline; this patch does
not claim all latency or standby failures are resolved.
