# Aliyun cross-turn context

The managed route now resumes the provider dialog ID after acknowledged
device playback, preserving a random client identity within that dialog.
Transport connections still close per turn. No persona/history override
or local transcript persistence was added.

The handle is scoped to the authenticated device WebSocket. Configuration,
agent, profile and memory-epoch changes discard it, as does logical session
finalization. A failed/cancelled turn never publishes a completed handle.
Disconnect/new login starts fresh; this is short-term context, not durable
memory. Switching from cascade does not import cascade transcript history.

Real provider verification: first RequestToRespond supplied a synthetic
code, then transport was closed. Second connection resumed dialog_id with
the same client identity and correctly recalled that code. Audio was
consumed/discarded locally; this was not a microphone/speaker acceptance test.

23 Aliyun and speech-to-speech tests passed, including strict device-drain
gating, consecutive gateway turns and reset on configuration change.
The gate test used ALIYUN_DIALOG_ENABLED=false to avoid local pilot .env
enabling the candidate before the test's explicit activation.

Reference: https://help.aliyun.com/zh/model-studio/multimodal-interaction-protocol
Start.input.dialog_id documents continuation of a previous conversation.
