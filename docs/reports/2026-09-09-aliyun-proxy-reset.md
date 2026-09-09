# Aliyun connection reset after wake

- Live gateway accepted the device connection, but later upstream opens failed.
- A direct WebSocket probe failed with ConnectionResetError before any audio.
- Clash runtime GET /configs reported global mode, while its on-disk configuration specified rule mode. Logs confirmed Aliyun traffic used GLOBAL.
- Restored runtime rule mode through the local named-pipe controller. No credentials or subscription configuration changed.
- A fresh WebSocket probe succeeded. Clash logged DomainSuffix(aliyuncs.com) using DIRECT.
- Controlled 2.61-second Opus -> PCM -> Aliyun audio probe returned final transcript (13 characters) at 3.83 seconds, response text at 4.84 seconds, and audio_done/done at 7.08 seconds from probe input start. No speaker playback was requested.

This verifies upstream recovery, not live microphone intelligibility or physical playback. The earlier short-upload problem and the no-camera VAD tail change still require live utterance acceptance. Runtime mode can be changed again from Clash; do not route domestic voice traffic through GLOBAL during this comparison.
