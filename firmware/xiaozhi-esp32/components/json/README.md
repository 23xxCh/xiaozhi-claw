# IDF 6 cJSON compatibility shim

The pinned `esp_emote_gen_player` commit still declares a public dependency on
the ESP-IDF 5 component name `json`. ESP-IDF 6 resolves the same public headers
and implementation as `espressif__cjson`.

This empty component preserves the upstream dependency name and forwards it to
the maintained component. It contains no JSON implementation of its own.
