# Aliyun connection reset after wake

- Live gateway accepted the device connection, but later upstream opens failed.
- A direct WebSocket probe failed with ConnectionResetError before any audio.
- Clash runtime GET /configs reported global mode, while its on-disk configuration specified rule mode. Logs confirmed Aliyun traffic used GLOBAL.
- Restored runtime rule mode through the local named-pipe controller. No credentials or subscription configuration changed.
- A fresh WebSocket probe succeeded. Clash logged DomainSuffix(aliyuncs.com) using DIRECT.
- Controlled 2.61-second Opus -> PCM -> Aliyun audio probe returned final transcript (13 characters) at 3.83 seconds, response text at 4.84 seconds, and audio_done/done at 7.08 seconds from probe input start. No speaker playback was requested.

This verifies upstream recovery, not live microphone intelligibility or physical playback. The earlier short-upload problem and the no-camera VAD tail change still require live utterance acceptance. Runtime mode can be changed again from Clash; do not route domestic voice traffic through GLOBAL during this comparison.

## Subsequent live verification

After recovery the real device completed two consecutive turns in conversation 7ad711b2-1d4a-4722-94c8-aec89276fea4: d90bf779-6171-4a7b-bba6-c4f6a612c9ce and 69874854-6449-420c-b54b-83c04eb8e843. Uploaded PCM durations were 4.14 and 2.40 seconds. Both produced final transcripts and completed outcomes; serial states showed speaking then automatic listening. Device speaker-start metrics were 2694 and 3702 ms. This establishes two live completed turns, not statistical reliability or subjective audio quality.
