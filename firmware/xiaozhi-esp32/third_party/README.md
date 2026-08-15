# Hensun local component overrides

These components are kept as local ESP-IDF dependency overrides so the fixes
used by the Hensun firmware are reproducible from a clean checkout.

- `esp_emote_gen_player` is based on upstream commit
  `7139b46c6616d466ff153cb9d2ddf63661434f22`. Hensun adds optional PSRAM
  preloading for animation payloads. The flash mapping is retained until normal
  teardown because changing mappings while ESP-SR is executing on the other
  core can trigger an ESP32-S3 MMU cache fault.
- `esp_emote_gfx` is based on registry version `3.0.5`, repository commit
  `47f9fd04a44d5d2b5dba8c14f73e4fc0f76b21f9`. Hensun fixes the dirty-area sync
  boundary and bounds Huffman decoding with an internal-node pool.

Do not replace these overrides with freshly downloaded managed components
without re-running the Hensun firmware tests and the ESP32 soak test.
