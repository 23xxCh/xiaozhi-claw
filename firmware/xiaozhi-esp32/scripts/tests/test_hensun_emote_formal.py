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

if __name__ == "__main__":
    unittest.main()
