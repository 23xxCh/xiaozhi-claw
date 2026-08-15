# Hensun maintainability refactor validation — 2026-08-15

## Outcome

The refactor is complete at the automated-validation level and the current
landscape gold sample boots successfully after a partial COM6 flash. The
control plane, realtime gateway, and web console are running locally from this
worktree.

The remaining acceptance work is physical interaction: 30 spoken turns,
immediate volume/brightness application while the device WebSocket is active,
camera capture recovery, and the eight-hour idle soak.

## Architecture delivered

- Versioned `HardwareProfile`, `DisplayProfile`, and `ProductVariant` inputs
  generate temporary firmware headers, sdkconfig defaults, CMake variables,
  and profile metadata under `build/generated`.
- Hensun display responsibilities are separated into panel, renderer, emotion
  mapping, speech envelope, and camera preview components.
- Device WebSocket and device-configuration contracts generate Python, C++,
  TypeScript, and JSON fixtures from one source, with a reproducibility check.
- FastAPI OpenAPI generates the typed frontend client.
- Runtime model selection goes through a provider registry. Settings are split
  into security, control-plane, gateway, and provider groups.
- Realtime session duties are separated into connection lifecycle, turn state,
  playback, tools, usage, snapshots, and summaries.
- Device configuration stores versioned desired/applied values while retaining
  compatibility with the existing volume and brightness fields.

## Automated gates

| Gate | Result |
|---|---|
| Backend pytest | pass, 1 skip |
| Ruff | pass |
| mypy | pass, 61 source files |
| Contract generation check | pass |
| Hensun firmware unit tests | pass, 56 tests |
| Release source/configuration gate | pass |
| Frontend API generation check | pass |
| Frontend ESLint | pass |
| Frontend production build | pass |
| Playwright browser acceptance | pass, 5 tests |
| Alembic copy upgrade/downgrade/upgrade | pass |

The first portrait build hit an unexplained Windows high-concurrency compiler
subprocess exit. The exact failed object compiled successfully with `ninja -j1`,
and the unified portrait build then passed. This is recorded as a toolchain
stability concern rather than hidden as a clean first-pass result.

## Database preservation

- Pre-refactor backup:
  `run/backups/hensun-lan-pre-maintainability-20260815-111645.db`
- Local-pilot startup backup:
  `run/backups/20260815-120956/hensun-lan.db`
- Current Alembic revision: `20260815_06`
- Preserved counts: users 1, devices 1, agents 1, device configurations 1,
  usage profiles 1.

## Firmware matrix

| Variant | ZIP SHA256 |
|---|---|
| official | `17269b8957f655dae269226c5e6495b51428b6e09fcd8f8e5182fcdf4af9d13b` |
| selfhosted landscape | `2f6f82659492a676abbe94b83b1d89feffc4d0e9817de3efb879c91722d8f0d3` |
| selfhosted portrait | `9c0eb4d17b2575448dc6a1e0698a50c00792b36488ba432d024fa425657d1f07` |
| emote lab | `f7c3a4f400b4f64b55f3ea94ef29d597047e03856a5d32462a4dd04e1f1998d9` |

All four variants are packaged independently. The landscape application has
35% of its smallest OTA partition free; its model and emote resources fit the
profile-declared partition limits.

## COM6 evidence

Only these partitions were written:

- `0x20000`: landscape application
- `0xB00000`: landscape emote resources

NVS, Wi-Fi, device credentials, the partition table, and speech models were not
erased or overwritten. Esptool verified both written hashes and hard-reset the
device.

The subsequent boot log confirmed:

- all 9 landscape animations mounted and preloaded into PSRAM;
- camera initialization remains deferred until first capture;
- saved Wi-Fi connected and obtained an address;
- bootstrap reached the local control plane with HTTP 200;
- application entered `idle`;
- custom `ni hao xiao can` MultiNet command loaded;
- no panic, watchdog reset, or emote mount failure was observed.

The device does not keep a permanent WSS session while idle, so the database
still shows the previous conversation session as offline. A spoken or button
initiated turn is required to finish the WSS/config-ack checks.

## Manual acceptance still required

1. Complete 30 spoken turns, including interrupt and tool calls.
2. While a turn is active, change volume and brightness and confirm config ACK.
3. Change each engineering parameter and confirm its declared next-turn or
   next-boot activation boundary.
4. Trigger the first camera capture and confirm the emote renderer recovers.
5. Leave the device idle for eight hours and inspect heap, reconnect, watchdog,
   and display-freeze evidence.

No remote push or pull request was performed.
