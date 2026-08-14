# Hensun Emote Lab V1

## Goal

Replace the engineering-style cyan icon faces with six product-grade black-and-white motion identities before expanding the 60-state semantic catalog.

## Visual contract

- Canvas: 240 x 320 portrait at 20 FPS.
- Background: `#000000`; face: `#F7F7F2`; antialiasing grays only.
- Every clip starts and ends at the same quiet two-pill bridge pose.
- Every clip has entrance, repeatable loop, and return stages encoded in its filename.
- Emotion must be readable from eye and mouth silhouette without labels or decorative emoji icons.
- The artwork is original Hensun material. Espressif examples are used only to study the public motion workflow.

## Runtime contract

The experimental `hensun-cam-emote-lab-v1` build mounts `hensun_emote_lab_v1.bin` from the `emote_gen` partition. Normal transitions drain the active return segment; urgent transitions cut immediately. Camera capture stays compiled, while on-screen camera preview remains disabled in this lab build.

The official and self-hosted production builds continue to use `HensunFaceDisplay` until the GFX camera-preview adapter and live PCM mouth overlay are separately accepted.
