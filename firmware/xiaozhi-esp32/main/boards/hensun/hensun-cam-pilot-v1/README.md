# Hensun CAM Pilot V1

Commercial software-pilot identity for the seller's ESP32-S3-CAM V2.0
expansion board. It is derived from the upstream
`bread-compact-wifi-s3cam` pin map and the seller's v2.2.6 source package.

## Enabled hardware

- ESP32-S3 N16R8
- digital I2S microphone on GPIO 1/2/42
- simplex I2S speaker output on GPIO 39/40/41
- ST7789 320x240 landscape display in the self-hosted build; the official
  fallback keeps its separate 240x320 portrait profile
- OV3660 camera using the seller-confirmed pin map
- boot/chat button on GPIO 0
- phone-friendly `Xiaozhi-XXXX` hotspot Wi-Fi provisioning
- long-press BOOT for about two seconds to enter Wi-Fi provisioning; double-click keeps the internal expression showcase

## Company expression runtime

The self-hosted landscape display mounts the original black-and-white GFX
resource pack from the `emote_gen` partition. Its 12 runtime states are sleep,
wake, idle, listening, thinking, speaking, happy, caring, curious, surprised,
confused and alert. Speaking has four extra PCM-volume variants, so its mouth
follows the actual speaker output rather than the LLM text.

The Hensun 60-scene development pack remains the semantic vocabulary; close
scenes intentionally route to one of these readable runtime states instead of
requiring 60 independent full-frame animations. See
[`FACE_EVENT_CONTRACT.md`](FACE_EVENT_CONTRACT.md) for the event boundary and
[`EMOTE_LAB_SPEC.md`](EMOTE_LAB_SPEC.md) for the deterministic asset pipeline.

`tts.start` keeps a thinking face until the first PCM data actually reaches the
speaker. The device sends `drained` only after the playback queue is empty;
then it shows a bounded 0.8-second response finish before idle and later sleep.
This prevents a silent speaking face from being treated as a successful reply.

Camera previews temporarily cover the face and restore it when the preview
expires. This keeps the camera available while testing the expression system.

Charging and battery scenes are reserved for future hardware and can be
injected in the showcase or by a trusted product event, but this USB-powered
CAM pilot does not generate battery events locally.

The camera remains initialized and is available through XiaoZhi's camera MCP
tool. The battery manager, power-save shutdown, GPIO lamp test and GPIO 48
status LED are deliberately absent from this pilot build. This board has no
audio codec or reference microphone, so it cannot satisfy the production
full-duplex AEC gate.

The board has two isolated firmware variants. `hensun-cam-official-v1` uses
XiaoZhi's official bootstrap and is managed from the XiaoZhi console.
`hensun-cam-selfhosted-v1` defaults to the reserved `.invalid` domain and must
receive an approved Hensun bootstrap URL at build time. Their distinct OTA
identities prevent one channel from silently updating the other.

## Build

Use ESP-IDF 6.0.2:

```bash
python scripts/build.py hensun/hensun-cam-pilot-v1 \
  --name hensun-cam-official-v1 \
  --language zh-CN \
  --wake-word nihaoxiaozhi
```

For normal development, use the repository-level `scripts/build_firmware.ps1`
wrapper because it validates and injects the self-hosted bootstrap URL without
leaving that URL in the tracked board configuration.

Do not flash `hensun-desk-v1` to this board. That identity targets the
ESP32-S3-BOX-3 codec and display layout.
