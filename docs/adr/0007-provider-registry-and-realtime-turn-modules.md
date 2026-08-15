# ADR 0007: Provider registry and realtime turn modules

- Status: Accepted
- Date: 2026-08-15

## Context

Model presets already stored ASR, LLM, and TTS provider IDs, but the realtime
gateway always constructed the same Qwen/DeepSeek/Qwen chain. The device WSS
coordinator also owned authentication, audio collection, provider calls,
playback handshakes, configuration acknowledgements, usage writes, and memory
summaries in one file.

## Decision

Provider IDs are resolved through `RealtimeProviderBundle`, which now acts as a
registry for ASR factories, LLM adapters, and TTS factories. A provider is added
by implementing the existing protocol and registering it; the turn coordinator
does not branch on vendor names. Missing providers fail with a stable kind and
provider ID. Legacy no-argument provider methods remain temporarily for test and
older integration compatibility.

The flat environment-variable names remain stable, while `Settings` exposes
typed `security`, `control_plane`, `gateway`, and `providers` views. New runtime
code consumes those views so deployment configuration can be migrated without a
breaking environment rename.

Realtime responsibilities are separated as follows:

- `session.py`: authenticated connection lifecycle and incoming message routing.
- `turn.py`: one ASR → LLM/tools → TTS voice turn.
- `turn_state.py`: explicit seven-state voice turn state machine.
- `playback.py`: `start/ready/stop/drained` handshake.
- `snapshot.py`: immutable agent/model/voice configuration snapshot.
- `device_state.py`: heartbeat, device hello metadata, and configuration ACKs.
- `usage.py`: provider usage and aggregate cost writes.
- `summaries.py`: consent-gated encrypted session summaries.

## Consequences

- A `ModelPreset` provider ID now selects the runtime adapter.
- Adding a provider requires an adapter, registry entry, and tests, but no turn
  coordinator edit.
- The device protocol and audio parameters are unchanged.
- A batch DashScope preset is supported through adapters over the existing
  bounded fallback providers.
- Flat `Settings` fields are retained as a compatibility boundary and can be
  removed only in a later, separately migrated configuration change.
