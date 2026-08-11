# Hensun Desk V1

Commercial pilot board identity for the Hensun adult desktop assistant.

## Pilot hardware

The first 20-device pilot deliberately targets the ESP32-S3-BOX-3 hardware
layout. It provides the ES8311/ES7210 full-duplex audio path, device-side AEC,
a 320x240 display, a physical button, a speaker amplifier, and USB-C power.

This is a compatibility identity, not permission to flash the image to other
ESP32-S3 boards. A later custom PCB must receive a new board type after its
schematic and pin map are frozen.

## Safety defaults

- Wake-word audio upload is disabled.
- Device-side AEC is enabled.
- The OTA endpoint uses the `.invalid` top-level domain so a build cannot
  silently contact the upstream free service. Replace it through the approved
  release configuration before any hardware field test.
- The product has no camera, battery, motor, or charging dock.

## Build

```bash
python scripts/build.py hensun/hensun-desk-v1 \
  --name hensun-desk-v1 \
  --language zh-CN \
  --wake-word nihaoxiaozhi
```

Use ESP-IDF 6.0.2. Physical audio, display, wake-word, interruption, and OTA
tests are mandatory before this board definition can be called validated.
