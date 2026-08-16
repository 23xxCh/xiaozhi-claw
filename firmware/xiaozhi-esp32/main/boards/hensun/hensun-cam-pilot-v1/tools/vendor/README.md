# ESP Emote GFX converter

`eaf_converter_bg-gkbc_Rvp.wasm` is the converter shipped by ESP Emote GFX
Packer NEXT (`60c1a9c-dirty`) on 2026-08-14.

- Source page: https://emote-gfx-gen-tool-dev.pages.dev/
- SHA-256: `c3e1d8af3651df97188356ef7f1a42ab871928d9ca06341626cdbccb4437205d`
- Purpose: deterministic conversion of the six original Hensun GIF sources to
  the EAF payload consumed by `esp_emote_gen_player`.

The generated `hensun_emote_lab_v1.bin` remains reproducible without relying on
an in-app browser download.
