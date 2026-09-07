# Hensun Desk Pilot

Commercial pilot foundation for an 18+ desktop AI assistant and light
companion. The current 2026-09-07 candidate targets ESP32-S3 NOCAM, Wi-Fi and
half-duplex conversation, with the existing ASR/LLM/TTS route retained and an
optional Doubao speech-to-speech route under validation. Real synthetic-audio
probes passed both the direct PCM adapter and the complete bidirectional Opus
gateway (147 output packets), with simulated device ACKs. This is not a
microphone, speaker or whole-device acceptance result. The real database has
not been migrated, the Doubao catalog candidate remains disabled, and the
existing default route is unchanged. CAM builds and acceptance results below
are historical engineering references, not evidence that this NOCAM candidate
has passed. The product continues to exclude a motor, battery, charging dock,
child mode, virtual-romance positioning, and medical or psychological
treatment claims.

Current route decision, acceptance steps and evidence:
[`ADR 0006`](docs/adr/0006-selectable-voice-routes.md),
[`Doubao acceptance`](docs/runbooks/doubao-acceptance.md), and
[`2026-09-07 candidate report`](docs/reports/2026-09-07-doubao-candidate.md).

## Start here

This README is the portable repository overview. For the current Hensun
golden-sample state, development boundary, verified evidence and the exact
next actions, read the Chinese handoff documents first:

1. [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md) — the single-file account and
   agent handoff: current state, locked decisions, known failures, commands and
   next actions.
2. [`docs/START_HERE.md`](docs/START_HERE.md) — product boundary, architecture,
   code map and current truth.
3. [`docs/handoffs/2026-08-18-local-golden-sample.md`](docs/handoffs/2026-08-18-local-golden-sample.md)
   — decisions preserved from the development conversation and the dirty
   worktree inventory.
4. [`docs/handoffs/CHAT_CONTEXT.md`](docs/handoffs/CHAT_CONTEXT.md) — the
   condensed decision timeline and superseded approaches from the long-running
   development conversation.
5. [`docs/runbooks/local-golden-sample.md`](docs/runbooks/local-golden-sample.md)
   — local startup, build, safe flashing, rollback and physical acceptance.

The current continuation worktree is `E:\HENSUN_STABILITY_WT` on
`feature/hensun-stability-quality`. Do not assume that the repository's other
worktrees contain the same uncommitted changes.

## Repository status

This branch implements the first executable batch of the commercialization
plan:

- XiaoZhi ESP32 firmware fixed at upstream `v2.4.2`. The current CAM hardware
  has two isolated release channels: `hensun-cam-official-v1` for XiaoZhi's
  cloud and `hensun-cam-selfhosted-v1` for Hensun's cloud. `hensun-desk-v1`
  remains the later ESP32-S3-BOX-3 full-duplex hardware gate.
- A FastAPI control plane for factory registration, device bootstrap,
  atomic account claim, quota, opt-in encrypted summary memory, OTA manifests,
  audit events, agents, official model presets and staff roles.
- An independent ASGI realtime gateway for XiaoZhi-compatible WSS, streaming
  Qwen ASR, DeepSeek SSE, streaming Qwen TTS, abort and expression routing.
- A responsive Next.js customer console and internal role-aware entry point.
  Model credentials stay on the server and are never returned to the browser
  or flashed to the ESP32.

The WeChat mini program, provider-specific non-compatible speech adapters, payment callbacks,
hardware validation, certification, regulatory filings, and production PKI are
separate gated deliverables. Nothing in this repository is a certification or
permission to sell hardware.

## Two firmware editions

### 1. XiaoZhi official edition

Build or flash the firmware that uses XiaoZhi's official bootstrap:

```powershell
.\scripts\build_firmware.ps1 -Variant official
.\scripts\flash_firmware.ps1 -Variant official
```

After flashing, connect a phone to the device's `Xiaozhi-XXXX` hotspot and enter
the Wi-Fi credentials. Device activation and agent configuration remain in
`https://xiaozhi.me/console/agents`.

### 2. Hensun self-hosted edition

Copy `.env.example` to `.env`, set `PROVIDER_MODE=custom`, then configure the
separate ASR, TTS and LLM URLs, keys and model names. The default provider
protocols are OpenAI-compatible transcription, speech and Chat Completions.
For the current Qwen/DeepSeek pilot, use `ASR_PROTOCOL=qwen-chat-completions`
with the DashScope compatible base URL and `TTS_PROTOCOL=dashscope-generation`
with DashScope's native generation endpoint. Qwen's OpenAI-compatible ASR is
the batch `qwen3-asr-flash` model; `qwen3-asr-flash-realtime` remains a separate
WebSocket adapter and is not silently treated as HTTP. TTS audio is normalized
with FFmpeg before it reaches the device. The self-hosted firmware and gateway
also use a `reply_id` playback handshake: the device acknowledges `state=ready`
before 60 ms Opus frames are paced downstream, and acknowledges `state=drained`
only after its decoder and speaker queues are empty. Messages without
`reply_id` retain the upstream XiaoZhi behavior for official-cloud compatibility.

For the current LAN pilot:

```powershell
.\scripts\start_local_pilot.ps1 -HostAddress '192.168.5.49'
.\scripts\build_firmware.ps1 -Variant local `
  -BootstrapUrl "http://192.168.5.49:8000/v1/device/xiaozhi-bootstrap"
.\scripts\flash_firmware.ps1 -Variant local `
  -BootstrapUrl "http://192.168.5.49:8000/v1/device/xiaozhi-bootstrap"
```

`local` produces `hensun-cam-selfhosted-landscape-local-v1`, the 320x240
landscape golden-sample firmware. The generic `selfhosted` variant remains a
separate Hensun-cloud build and must not be substituted silently.
The flash script re-detects the online CH340/CH341 port immediately before
writing and only permits the application and, when present, `emote_gen`
segments. It never runs the full-project flash target.

Factory provisioning writes each device's one-time credential into NVS after
the firmware flash. Read the secret from the single-use factory response and
enter it without placing it in shell history:

```powershell
$secret = Read-Host "Device secret" -AsSecureString
.\scripts\flash_device_identity.ps1 -DeviceSecret $secret -Port COM6
```

The script writes only the 16 KiB NVS partition at `0x9000` and deletes the
temporary plaintext image after flashing. Run it before customer Wi-Fi setup,
because writing the factory NVS partition also clears prior Wi-Fi settings.

Production must replace the LAN URLs with public `https://`/`wss://` endpoints.
API keys belong only in the backend environment; do not put them into firmware,
web pages or the mini program.

## Quick start

```powershell
Set-Location '<your xiaozhi-claw worktree>'
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python -m uvicorn backend.app.main:app --reload
```

In a second terminal, start the realtime gateway:

```powershell
.\.venv\Scripts\python -m uvicorn backend.realtime.main:app --reload --port 8001
```

Start the web console:

```powershell
cd web
npm install
Copy-Item .env.example .env.local
npm run dev
```

Open `http://127.0.0.1:3000` for the console and
`http://127.0.0.1:8000/docs` for OpenAPI. The customer console uses a six-digit
email verification code. In development mode the page clearly displays the local
test code; it does not claim that an email was sent. Production must set
`EMAIL_DELIVERY_MODE=smtp`, SMTP host/credentials/from-address, HTTPS and secure
cookies. The legacy development endpoint and mock AI are disabled automatically
when `APP_ENV=production`.

For the verified LAN pilot on the Hensun development computer, start or stop all three
processes with:

```powershell
.\scripts\start_local_pilot.ps1
.\scripts\stop_local_pilot.ps1
```

The launcher migrates the configured database, creates a production web build, records
only exact child PIDs under the ignored `run/` directory, and opens the LAN console after
all readiness probes pass.

The current local display uses a 320x240 landscape profile. The backend can
parse ten controlled `[[face:...]]` labels, but the current physical runtime
has three independently integrated talking faces (`neutral`, `happy`, and
`caring`). Do not describe the remaining labels as completed device assets.

Run the database migration before either backend process in production:

```powershell
.\.venv\Scripts\python -m alembic upgrade head
```

For an existing pre-Alembic pilot database, back it up first, then run
`python -m alembic stamp 20260812_00` once before `upgrade head`.

Run the backend checks:

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python scripts\release_gate.py
```

Run a real local TLS WebSocket handshake and device-event delivery smoke test:

```powershell
.\.venv\Scripts\python scripts\wss_smoke.py
```

The smoke test creates a short-lived self-signed localhost certificate and an
isolated temporary database, then removes both. It proves the XiaoZhi-compatible
bootstrap response, short-lived device session, `wss://` handshake, claim flow,
and whitelisted face-event delivery without touching a physical ESP32. Never
deploy its self-signed certificate; production devices must connect through a
publicly trusted TLS certificate and a provisioned device secret.

Firmware host checks do not require a connected ESP32:

```powershell
cd firmware\xiaozhi-esp32
python -m unittest discover -s scripts\tests -v
python scripts\build.py --list-boards
```

An ESP-IDF 6.0.2 environment is required for a real build. The current CAM
board must use `hensun-cam-pilot-v1`; it provides simplex audio and cannot be
used to claim full-duplex AEC acceptance. A physical ESP32-S3-BOX-3 is still
required before validating `hensun-desk-v1`.

## Source provenance

`firmware/xiaozhi-esp32` is a source snapshot of
`78/xiaozhi-esp32@e8d8a4010788afd60f0c8aa3b2e3d0a7bb8f02e5` (`v2.4.2`).
The upstream MIT license remains at `firmware/xiaozhi-esp32/LICENSE`.
