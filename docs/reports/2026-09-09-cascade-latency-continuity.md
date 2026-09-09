# Cascade latency and continuity candidates

## Changes

- Keep the active Aliyun managed application role unchanged.
- Expose existing fast-chat as Aliyun ASR + DeepSeek + Aliyun TTS. Add volc-chat: Volc streaming ASR 1.0 + DeepSeek + Volc TTS 2.0. Existing mixed route remains available.
- Volc ASR uses the optimized bidirectional WebSocket endpoint with 180 ms Ogg Opus groups; no extra PCM transcoder. Response parsing validates frame boundaries and bounds decompression. Cancellation closes the reader and socket. ASR errors do not fall back to Aliyun.
- Share a random TTS section_id across sentences of one reply. A new TTS session gets a new ID. This enables supplier-side synthesis context for prosody; subjective improvement is not yet verified. Supplier context is not Hensun long-term memory.
- New ASR credentials are server-side only. Production admission remains gated by VOLC_ASR_VALIDATED and existing TTS validation. New catalog entry defaults disabled; enabled only in this local development database after controlled service checks. Existing roles are not migrated.
- ASR cost is unknown pending billing reconciliation. Its catalog numeric placeholder is not used as a known zero supplier charge.

## Evidence

- Real ASR access: 1.0 accepted; 2.0 returned HTTP 403. Do not advertise 2.0 availability with this credential.
- Synthetic spoken input through Opus and real ASR returned a 13-character transcript. Three controlled final-result timings after upload completion: 557, 104, 135 ms. These are diagnostic samples, not P50/P90 or device end-to-end benchmarks.
- Real Volc ASR -> DeepSeek -> Volc TTS completed twice, yielding 201574 and 317440 bytes PCM. Audio was discarded rather than played on the device.
- Decoder experiment with/without avioflags direct: both first PCM approximately 94 ms and identical output byte counts. No decoder flag change was retained.
- Regression commands: pytest test_volc_asr.py, test_volc_tts.py, test_providers.py, test_voice_routes.py, test_aliyun_dialog.py, test_s2s_gateway.py. All passed with the existing local Aliyun enable flag disabled only in the test shell.
- Restarted API and gateway; device reconnected. Local catalog admission passes: fast-chat 12 compatible enabled voices; volc-chat 3. Other inventory entries are not implicitly enabled.

## Remaining acceptance

Select each cascade route on the role page and compare normal conversation, long replies, stop, and follow-up listening. Capture speaker-start latency and listen for sentence-boundary pitch changes. Current managed application's measured 2.7/3.7-second turns remain the baseline; no claim that its latency changed. Do not lower the recently fixed 700 ms VAD tail merely to reduce a metric.

## Sources and rollback

- https://www.volcengine.com/docs/6561/1354869 — streaming protocol and resources, retrieved from the official getDocDetail API on 2026-09-09.
- https://www.volcengine.com/docs/6561/1598757 — TTS additions.section_id contract, retrieved the same way.
- Rollback: disable volc-chat locally and keep/select the prior managed application role. Existing credentials, roles and voice records remain intact. Removing section_id restores independent sentence synthesis without database migration.
