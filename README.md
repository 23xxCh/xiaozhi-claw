# Hensun Desk Pilot

Commercial pilot foundation for an 18+ desktop AI assistant and light
companion. The current CAM engineering build is USB-C powered and retains its
camera for development. It intentionally excludes a motor, battery, charging
dock, child mode, virtual-romance positioning, and medical or psychological
treatment claims.

## Repository status

This branch implements the first executable batch of the commercialization
plan:

- XiaoZhi ESP32 firmware fixed at upstream `v2.4.2`. The current CAM hardware
  has two isolated release channels: `hensun-cam-official-v1` for XiaoZhi's
  cloud and `hensun-cam-selfhosted-v1` for Hensun's cloud. `hensun-desk-v1`
  remains the later ESP32-S3-BOX-3 full-duplex hardware gate.
- A FastAPI modular monolith for factory registration, device bootstrap,
  atomic account claim, quota, opt-in encrypted summary memory, OTA manifests,
  audit events, and a XiaoZhi-compatible WebSocket control loop.
- Development mock providers plus configurable ASR, TTS and OpenAI-compatible
  LLM adapters. Model credentials stay on the server and are never flashed to
  the ESP32.

The WeChat mini program, provider-specific non-compatible speech adapters, payment callbacks,
hardware validation, certification, regulatory filings, and production PKI are
separate gated deliverables. Nothing in this repository is a certification or
permission to sell hardware.

## Two firmware editions

### 1. XiaoZhi official edition

Build or flash the firmware that uses XiaoZhi's official bootstrap:

```powershell
.\scripts\build_firmware.ps1 -Variant official
.\scripts\flash_firmware.ps1 -Variant official -Port COM6
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
with FFmpeg before it reaches the device.

For the current LAN pilot:

```powershell
.\scripts\start_lan_backend.ps1
.\scripts\build_firmware.ps1 -Variant selfhosted `
  -BootstrapUrl "http://192.168.5.49:8000/v1/device/xiaozhi-bootstrap"
.\scripts\flash_firmware.ps1 -Variant selfhosted -Port COM6 `
  -BootstrapUrl "http://192.168.5.49:8000/v1/device/xiaozhi-bootstrap"
```

Production must replace the LAN URLs with public `https://`/`wss://` endpoints.
API keys belong only in the backend environment; do not put them into firmware,
web pages or the mini program.

## Quick start

```powershell
cd "E:\AI TOY\xiaozhi-claw"
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env
.\.venv\Scripts\python -m uvicorn backend.app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`. Development login and mock AI are disabled
automatically when `APP_ENV=production`.

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
