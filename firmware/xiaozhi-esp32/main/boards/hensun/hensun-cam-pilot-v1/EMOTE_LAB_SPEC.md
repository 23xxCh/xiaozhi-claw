# Hensun Emote Lab V1

## Goal

Replace the engineering-style cyan icon faces with twelve product-grade black-and-white motion identities. The 60-scene product vocabulary is a semantic layer which maps into these twelve readable runtime states.

## Visual contract

- Canvas: 320 x 240 landscape at 20 FPS.
- Background: `#000000`; face: `#F7F7F2`; antialiasing grays only.
- Every clip starts and ends at the same quiet two-pill bridge pose.
- Every clip has entrance, repeatable loop, and return stages encoded in its filename.
- Emotion must be readable from eye and mouth silhouette without labels or decorative emoji icons.
- The artwork is original Hensun material. Espressif examples are used only to study the public motion workflow.

## Runtime contract

The experimental `hensun-cam-emote-lab-v1` build mounts `hensun_emote_lab_v1.bin` from the `emote_gen` partition. Normal transitions drain the active return segment; urgent transitions cut immediately. The packed set contains `sleep`, `wake`, `idle`, `listening`, `thinking`, `speaking` plus three PCM-driven speaking variants, `happy`, `caring`, `curious`, `surprised`, `confused`, and `alert`.

The self-hosted landscape build uses the same player with its real PCM mouth overlay. Camera capture stays compiled, while on-screen camera preview remains disabled in the lab build. The official fallback continues to use its independent portrait display profile and does not mount the GFX resource partition.
