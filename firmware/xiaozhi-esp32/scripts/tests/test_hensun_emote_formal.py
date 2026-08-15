import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"


class HensunEmoteFormalMergeTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((BOARD / "config.json").read_text(encoding="utf-8"))
        self.builds = {build["name"]: build for build in config["builds"]}
        self.header = (BOARD / "hensun_emote_lab_display.h").read_text(encoding="utf-8")
        self.source = (BOARD / "hensun_emote_lab_display.cc").read_text(encoding="utf-8")

    def test_selfhosted_uses_accepted_emote_engine_but_official_does_not(self):
        selfhosted = "\n".join(
            self.builds["hensun-cam-selfhosted-v1"]["sdkconfig_append"]
        )
        official = "\n".join(
            self.builds["hensun-cam-official-v1"]["sdkconfig_append"]
        )
        for option in (
            "CONFIG_USE_EMOTE_MESSAGE_STYLE=y",
            "CONFIG_FLASH_NONE_ASSETS=y",
            "CONFIG_USE_CUSTOM_WAKE_WORD=y",
            'CONFIG_CUSTOM_WAKE_WORD="ni hao xiao can"',
            'CONFIG_CUSTOM_WAKE_WORD_DISPLAY="你好小灿"',
            "CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=15",
            "CONFIG_SR_MN_CN_MULTINET5_RECOGNITION_QUANT8=y",
            'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions/v2/16m_hensun_emote_lab.csv"',
        ):
            self.assertIn(option, selfhosted)
            self.assertNotIn(option, official)

    def test_speaking_uses_four_rate_limited_audio_levels(self):
        for name in ("speaking_0", "speaking_1", "speaking", "speaking_3"):
            self.assertIn(f'"{name}"', self.source)
        self.assertIn("QuantizeSpeechLevel", self.source)
        self.assertIn("kSpeechSwitchMinIntervalMs", self.source)
        self.assertIn("speech_level_", self.header)
        self.assertIn("speaking_active_", self.header)

    def test_lcd_completion_callback_is_ready_before_render_task_starts(self):
        register_callback = self.source.index(
            "esp_lcd_panel_io_register_event_callbacks"
        )
        start_player = self.source.index("emote_gen_player_init")

        self.assertLess(register_callback, start_player)

        board = (BOARD / "hensun_cam_pilot_v1_board.cc").read_text(
            encoding="utf-8"
        )
        self.assertIn("io_config.trans_queue_depth = 10", board)

    def test_emote_assets_preload_before_audio_engine_starts(self):
        constructor = self.source.split(
            "HensunEmoteLabDisplay::HensunEmoteLabDisplay", 1
        )[1].split("HensunEmoteLabDisplay::~HensunEmoteLabDisplay", 1)[0]

        mount_assets = constructor.index("emote_gen_player_mount_assets")
        start_switch_task = constructor.index("xTaskCreate")
        self.assertLess(mount_assets, start_switch_task)
        self.assertIn(".preload_to_spiram = 1", constructor)
        self.assertIn(
            "Hensun emote assets preloaded before audio engine start", constructor
        )
        self.assertNotIn("OnAudioEngineReady", self.header)

    def test_emote_runtime_fixes_use_tracked_local_components(self):
        component_manifest = (ROOT / "main/idf_component.yml").read_text(
            encoding="utf-8"
        )
        player_assets = (
            ROOT
            / "third_party/esp_emote_gen_player/src/emote_gen_player_assets.c"
        ).read_text(encoding="utf-8")
        player_source = (
            ROOT / "third_party/esp_emote_gen_player/src/emote_gen_player.c"
        ).read_text(encoding="utf-8")
        gfx_render = (
            ROOT / "third_party/esp_emote_gfx/src/core/display/gfx_render.c"
        ).read_text(encoding="utf-8")
        eaf_decoder = (
            ROOT / "third_party/esp_emote_gfx/src/lib/eaf/gfx_eaf_dec.c"
        ).read_text(encoding="utf-8")

        self.assertIn("override_path: ../third_party/esp_emote_gen_player", component_manifest)
        self.assertIn("override_path: ../third_party/esp_emote_gfx", component_manifest)
        self.assertIn("MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT", player_assets)
        self.assertIn("flash mmap retained until teardown", player_assets)
        self.assertIn("handle->preloaded_assets[index]", player_source)
        copy_area = gfx_render.index(
            "gfx_area_copy(&disp->sync_pending.areas[sync_points], area)"
        )
        increment_sync = gfx_render.index("sync_points++;", copy_area)
        self.assertLess(copy_area, increment_sync)
        self.assertIn("EAF_HUFFMAN_MAX_NODES", eaf_decoder)
        self.assertIn("Huffman output overflow", eaf_decoder)

    def test_camera_uses_display_independent_rgb565_preview(self):
        display_header = (ROOT / "main/display/display.h").read_text(encoding="utf-8")
        camera_source = (ROOT / "main/boards/common/esp32_camera.cc").read_text(
            encoding="utf-8"
        )
        lvgl_header = (
            ROOT / "main/display/lvgl_display/lvgl_display.h"
        ).read_text(encoding="utf-8")

        self.assertIn("SetPreviewFrame", display_header)
        self.assertIn("SetPreviewFrame", lvgl_header)
        self.assertIn("SetPreviewFrame", self.header)
        self.assertIn("RotateRgb565Clockwise", self.source)
        self.assertIn("gfx_emote_lock", self.source)
        self.assertIn("SetPreviewFrame", camera_source)
        self.assertNotIn("dynamic_cast<LvglDisplay", camera_source)

    def test_formal_spec_preserves_camera_and_protocol_boundaries(self):
        spec = (BOARD / "EMOTE_FORMAL_MERGE_SPEC.md").read_text(encoding="utf-8")
        for phrase in (
            "不同时启动 LVGL",
            "四档说话幅度",
            "RGB565",
            "不擦除 NVS",
        ):
            self.assertIn(phrase, spec)

    def test_selfhosted_build_keeps_custom_wake_word_configuration(self):
        build_script_path = Path.cwd() / "scripts/build_firmware.ps1"
        self.assertTrue(build_script_path.is_file(), build_script_path)
        build_script = build_script_path.read_text(encoding="utf-8")
        self.assertIn('if ($Variant -eq "official")', build_script)
        self.assertIn('$firmwareBuildArgs += @("--wake-word", "nihaoxiaozhi")', build_script)
        self.assertNotRegex(
            build_script,
            r'--language zh-CN\s+`\s+--wake-word nihaoxiaozhi',
        )

    def test_device_secret_uses_dedicated_nvs_partition(self):
        ota = (ROOT / "main/ota.cc").read_text(encoding="utf-8")
        self.assertIn('nvs_flash_init_partition("hensun_keys")', ota)
        self.assertIn('nvs_open_from_partition("hensun_keys"', ota)

    def test_compiled_custom_wake_command_does_not_require_assets_index(self):
        source = (ROOT / "main/audio/wake_words/custom_wake_word.cc").read_text(
            encoding="utf-8"
        )
        compiled_branch = source.split("#ifdef CONFIG_CUSTOM_WAKE_WORD", 1)[1].split(
            "#else", 1
        )[0]
        self.assertIn("CONFIG_CUSTOM_WAKE_WORD_THRESHOLD", compiled_branch)
        self.assertIn("commands_.push_back", compiled_branch)
        self.assertNotIn("ParseWakenetModelConfig", compiled_branch)

if __name__ == "__main__":
    unittest.main()
