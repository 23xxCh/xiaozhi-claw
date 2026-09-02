# Hensun No-CAM Pilot V1

This pilot identity targets the seller's XZ-AI_KZB V1.7 and V1.8 expansion
boards with an ESP32-S3 N16R8 module and an ST7789 240x320 display.

## Verified hardware contract

The pin map was checked against the seller's V1.7/V1.8 schematics and the
matching 1.54/1.8/2.0-inch TFT wiring guide:

- digital I2S microphone: WS 4, SCK 5, data 6;
- MAX98357 speaker path: data 7, BCLK 15, LRCLK 16;
- ST7789 display: backlight 42, CS 41, DC 40, reset 45, MOSI 47, clock 21;
- buttons: chat/provisioning 0, volume up 38, volume down 39.

The board has no camera and no playback-reference microphone. Device-side AEC,
camera tools, battery telemetry and the seller's optional actuator firmware are
therefore intentionally disabled for the initial hardware smoke test.

## Build

Use ESP-IDF 6.0.2:

```bash
python scripts/build.py hensun/hensun-nocam-pilot-v1 \
  --name hensun-nocam-selfhosted-v1 \
  --language zh-CN \
  --wake-word nihaoxiaozhi
```

The tracked OTA URL uses the reserved `.invalid` domain. A reviewed local or
staging bootstrap URL must be injected by the repository build wrapper before
the board is connected to Hensun services.

The 16 MiB layout reserves a separate 16 KiB `hensun_keys` NVS partition at
`0x800000`; generated assets begin at `0x804000`. The device credential remains
isolated from both generic Wi-Fi NVS and the assets image.
