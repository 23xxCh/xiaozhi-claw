# Prosody and follow-up investigation

User reports pitch jumps on both Qwen and Volc TTS, plus premature standby.
Shared SentenceBuffer previously forced the first segment after eight
characters and subsequent segments at 32 characters. Each segment becomes
an independent synthesis request, potentially resetting sentence prosody.
Defaults now prefer sentence punctuation, clauses after 40 characters and
a 120-character unpunctuated fallback. This may increase first-text latency.
Real acoustic improvement is not yet verified.

39 provider/face tests and 26 gateway tests passed; the known OpenAPI snapshot
test was excluded. Local gateway restarted and readiness returned 200.

Standby is unresolved. Logs show completed turns, empty ASR turns, and one
rejected connection followed by successful reconnect. A 50-second serial
capture showed ongoing mic levels and server reconnect but no wake event.
User then reported that the wake phrase no longer worked. A new controlled
wake/physical-button capture was requested; do not infer disabled wake
detection merely from absent wake events. No firmware changed or flashed.
