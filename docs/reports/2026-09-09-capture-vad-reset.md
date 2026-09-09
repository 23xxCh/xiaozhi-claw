# Capture VAD reset candidate

Serial evidence: after playback drained at 6332078 ms, follow-up listening
started at 6332878 ms. VAD started at 6333188 and ended at 6333248 (60 ms),
ending capture before a normal follow-up. Firmware then timed out waiting for
a reply at 6344538. A BOOT-started input immediately beforehand produced a
transcript and completed playback. No recorded audio is retained.

The AFE remains active for wake detection. Enabling voice processing previously
did not invalidate that capture epoch, and buffer reset did not reset VAD.
The candidate invalidates the epoch on a disabled-to-enabled capture transition
and resets VAD in the existing fetch-task reset path. The no-camera board also
does not play its popup into an already-active microphone.

Host checks: 167 tests, one skipped, no failures. Hardware acceptance remains
required: wake then pause then speak; BOOT input; three follow-up turns; short
answers; cancellation. This candidate is not evidence of improved recognition.

ESP-IDF 6.0.2 no-camera local variant compiled successfully (2,791,232 bytes).
COM6 application-only write at 0x20000 completed with hash verification; NVS,
partition table and assets were not written. On boot the control service on
port 8000 was found absent and restarted. Conversation acceptance is pending.
