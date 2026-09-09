# Aliyun managed application comparison

Authorized test: active device role switched using update_agent to
aliyun-dialog / aliyun-app-default, config version 5. Previous Volc TTS
selection is backed up under run/backups/aliyun-switch-*.json.

Confirmed configured application matches the user's application ID. Real
connection reached Listening in 0.38 seconds. A separate controlled
RequestToRespond prompt produced 30 text characters and 306720 PCM bytes,
fully received by 3.11 seconds including connection. No physical playback
was claimed for this text-input probe.

The 50-second physical serial capture showed mic activity but no new
dialogue during the observation window. User weather/news/continuity
feedback remains pending. No firmware changed or flashed.

Known limit: the adapter creates a new upstream session each turn and does
not pass prior dialog_id/history. Console context count does not establish
device multi-turn continuity. Keep this route a comparison candidate.
