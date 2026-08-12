# Hensun face asset provenance

## Source and authorization

- Source package: `HensunAI-60场景表情开发包-V1.0`
- Style version: `HENSUN_FACE_V1.0`
- Source manifest SHA256: `ef297b34bb0d7e33915392513b2142d5a9b47bac912bd87e2f2b4151c79693a6`
- Authorization record: on 2026-08-12, the product owner confirmed that this package was developed by the company and may be reused in this product.

This file records the product owner's provenance statement for engineering traceability. It is not an independent legal opinion.

## Firmware derivation

The 1536x1024 PNG files remain high-resolution design masters and are not copied into firmware. `tools/generate_face_assets.py` imports the package's own `draw_symbol` implementation and produces:

- a compact 60-scene catalog snapshot;
- 36 reusable 48x48 LVGL A4 alpha masks;
- a scene-to-symbol and scene-to-accent lookup table.

The masks occupy about 41 KiB of flash in total and allocate no full-screen bitmap buffer at runtime. LVGL recolors each mask using the scene accent color.

## Reproduction

From the firmware repository root:

```powershell
python main/boards/hensun/hensun-cam-pilot-v1/tools/generate_face_assets.py `
  --source-package "E:\AI TOY\HensunAI-60场景表情开发包-V1.0\HensunAI-60场景表情开发包-V1.0"

python main/boards/hensun/hensun-cam-pilot-v1/tools/generate_face_assets.py `
  --source-package "E:\AI TOY\HensunAI-60场景表情开发包-V1.0\HensunAI-60场景表情开发包-V1.0" `
  --check
```
