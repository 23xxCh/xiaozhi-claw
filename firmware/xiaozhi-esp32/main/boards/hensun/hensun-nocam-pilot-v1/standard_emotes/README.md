# Standard emotes for the Hensun No-CAM landscape display

These GIFs were derived from the user-supplied `标准版表情包.zip` with SHA-256
`250C7456CE1456F6B72CF0CE2263BABA8F9F896380DA11B968DCFEDCCE74A93B`.
The source files are 384x288 animations. The tracked runtime assets preserve the
4:3 composition while scaling each animation to 320x240 at 10 FPS, filling the
320x240 landscape TFT without margins.

| Runtime asset | Source label |
|---|---|
| `neutral.gif` | 默认 |
| `shy.gif` | 害羞 |
| `sad.gif` | 难过 |
| `angry.gif` | 生气 |
| `surprised.gif` | 惊讶 |
| `sleepy.gif` | 困倦 |
| `confused.gif` | 疑惑 |
| `caring.gif` | 关爱 |
| `silly.gif` | 调皮 |

The board-specific display adapter maps cloud emotion aliases onto these nine
runtime names. Unknown values fall back to `neutral`. Its status and subtitle
bars keep their text and icons but use transparent backgrounds so the animation
remains visible across the entire panel.

Conversation states use the existing animations: standby uses `sleepy`, listening
uses `surprised`, waiting for a reply uses `confused`, and the bounded playback
settle uses `caring`. Replies retain cloud-selected emotions (default `neutral`);
the firmware does not randomly cycle emotions or override them when speech starts.
