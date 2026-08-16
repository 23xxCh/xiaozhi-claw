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
