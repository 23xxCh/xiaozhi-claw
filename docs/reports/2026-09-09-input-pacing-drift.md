# Input pacing drift

Live turn c0486381-c3e5-44ba-aad6-42f0b489bfcc delivered 32 device packets (1.92 s PCM equivalent), but at device input completion the upload consumer had reached only 10880 bytes (0.34 s). This shows backlog, not proof that all backlog originates in pacing.

The shared S2S uploader advanced its deadline from the time each send completed. Every send duration and timer overshoot therefore accumulated across a turn. Changed it to advance from the audio clock, with catch-up capped at 100 ms after a stall. This preserves bounded pacing and does not drop audio or lower device silence detection.

A deterministic regression injects 5 ms send cost and 10 ms timer overshoot on each 20 ms chunk. Two seconds of audio remains about two seconds instead of stretching with accumulated overhead. The input pacing, Aliyun adapter and S2S gateway tests passed (24 tests). Added per-turn drain_ms from device input completion to upstream end_input completion.

Restarted only the local gateway; device reconnected. A controlled real Opus-to-Aliyun probe returned final transcript at 3.49 s from start for a 2.6 s utterance, then text at 4.75 s and generation completion at 6.38 s. This is not speaker-start latency or a paired benchmark: the synthesized audio and upstream response may vary. New live-device latency verification remains pending.
