# Hensun face animation specification

## Goal

Make the 60-scene face feel alive on the 240x320 ST7789 display without adding
video decoding, full-screen frame buffers, cloud protocol fields, or work on the
audio and camera paths.

## Interface and data flow

- Input: the existing `HensunFaceState` plus the existing 20 FPS
  `animation_frame_` counter.
- Output: small LVGL position, size, opacity, image scale and image rotation
  changes applied by `HensunFaceDisplay::RenderFace()`.
- Flow: XiaoZhi/device event -> face state -> motion family -> bounded per-frame
  transforms -> existing LVGL objects.

No new WebSocket message or XiaoZhi console prompt format is required.

## Motion families

| Family | Scenes | Motion |
|---|---|---|
| Calm | idle and ordinary greetings | slow one-pixel breathing and natural blink |
| Listen | listening and wake-word | eye drift, breathing eyes and pulsing listen symbol |
| Think | thinking, clarification and curiosity | horizontal eye scan plus floating/tilting symbol |
| Speak | answer/result-ready | mouth cadence plus a small whole-face bob |
| Celebrate | happy, laughter, affection and achievements | cheek bounce, face lift and symbol pop |
| Sleep | sleep and fatigue | slow vertical breathing and drifting moon/thought symbol |
| Alert | alarm, reminder and urgent hardware faults | two-pixel shake and small symbol swing |
| Status | pairing, network, charge, volume and timers | restrained symbol pulse |
| Restrained | content safety and user crisis | entry easing only; no looping bounce or shake |

Every state has a short eight-frame entry easing, including states without a
looping motion family.

## Safety and performance boundaries

- Animation remains at the existing 50 ms timer period (20 FPS).
- Position changes are at most 6 pixels during entry and at most 2 pixels in a
  loop, except eye-only scan up to 4 pixels.
- Symbol scale stays between 224 and 272 where LVGL's neutral scale is 256.
- Symbol rotation stays within 6 degrees.
- Opacity changes are slow breathing effects; no full-screen or high-frequency
  flashing is introduced.
- Camera preview continues to hide the face layer, and animation stops doing
  visible work while a preview is active.
- Hardware acceptance target: average LVGL face update below 8 ms and maximum
  below 30 ms during the 60-scene showcase.
