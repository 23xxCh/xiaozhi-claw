import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"


class HensunCamPilotBoardTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((BOARD / "config.json").read_text(encoding="utf-8"))
        self.builds = {build["name"]: build for build in self.config["builds"]}
        self.pins = (BOARD / "config.h").read_text(encoding="utf-8")
        self.source = (BOARD / "hensun_cam_pilot_v1_board.cc").read_text(
            encoding="utf-8"
        )
        self.face_header = (BOARD / "hensun_face_display.h").read_text(
            encoding="utf-8"
        )
        self.face_source = (BOARD / "hensun_face_display.cc").read_text(
            encoding="utf-8"
        )
        self.event_contract = (BOARD / "FACE_EVENT_CONTRACT.md").read_text(
            encoding="utf-8"
        )

    def test_has_isolated_official_and_selfhosted_variants(self):
        self.assertEqual(self.config["manufacturer"], "hensun")
        self.assertEqual(self.config["type"], "hensun-cam-pilot-v1")
        self.assertEqual(
            set(self.builds),
            {"hensun-cam-official-v1", "hensun-cam-selfhosted-v1"},
        )

        official = "\n".join(self.builds["hensun-cam-official-v1"]["sdkconfig_append"])
        selfhosted = "\n".join(
            self.builds["hensun-cam-selfhosted-v1"]["sdkconfig_append"]
        )
        for sdkconfig in (official, selfhosted):
            self.assertIn("CONFIG_USE_HOTSPOT_WIFI_PROVISIONING=y", sdkconfig)
            self.assertIn("CONFIG_USE_ESP_BLUFI_WIFI_PROVISIONING=n", sdkconfig)
            self.assertIn("CONFIG_SEND_WAKE_WORD_DATA=n", sdkconfig)
        self.assertIn("api.tenclass.net", official)
        self.assertNotIn("api.hensun", official)
        self.assertIn("api.hensun.invalid", selfhosted)
        self.assertNotIn("api.tenclass.net", selfhosted)

    def test_pin_map_matches_the_seller_cam_v2_board(self):
        expected = {
            "AUDIO_I2S_MIC_GPIO_WS": 1,
            "AUDIO_I2S_MIC_GPIO_SCK": 2,
            "AUDIO_I2S_MIC_GPIO_DIN": 42,
            "AUDIO_I2S_SPK_GPIO_DOUT": 39,
            "AUDIO_I2S_SPK_GPIO_BCLK": 40,
            "AUDIO_I2S_SPK_GPIO_LRCK": 41,
            "BOOT_BUTTON_GPIO": 0,
            "CAMERA_PIN_D0": 11,
            "CAMERA_PIN_D1": 9,
            "CAMERA_PIN_D2": 8,
            "CAMERA_PIN_D3": 10,
            "CAMERA_PIN_D4": 12,
            "CAMERA_PIN_D5": 18,
            "CAMERA_PIN_D6": 17,
            "CAMERA_PIN_D7": 16,
            "CAMERA_PIN_XCLK": 15,
            "CAMERA_PIN_PCLK": 13,
            "CAMERA_PIN_VSYNC": 6,
            "CAMERA_PIN_HREF": 7,
            "CAMERA_PIN_SIOC": 5,
            "CAMERA_PIN_SIOD": 4,
            "DISPLAY_BACKLIGHT_PIN": 38,
            "DISPLAY_MOSI_PIN": 20,
            "DISPLAY_CLK_PIN": 19,
            "DISPLAY_DC_PIN": 47,
            "DISPLAY_RST_PIN": 21,
            "DISPLAY_CS_PIN": 45,
        }
        for symbol, gpio in expected.items():
            self.assertRegex(
                self.pins,
                rf"#define\s+{symbol}\s+GPIO_NUM_{gpio}\b",
                symbol,
            )

    def test_product_build_is_simplex_keeps_camera_and_excludes_battery(self):
        self.assertIn("NoAudioCodecSimplex", self.source)
        self.assertIn("Esp32Camera", self.source)
        self.assertIn("GetCamera", self.source)
        self.assertIn("SetVFlip(true)", self.source)
        self.assertNotIn("PowerManager", self.source)
        self.assertNotIn("PowerSaveTimer", self.source)
        self.assertNotIn("LAMP_GPIO", self.pins)
        self.assertEqual(len(re.findall(r"\bDECLARE_BOARD\(", self.source)), 1)

    def test_build_chain_selects_the_new_board(self):
        kconfig = (ROOT / "main/Kconfig.projbuild").read_text(encoding="utf-8")
        cmake = (ROOT / "main/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("config BOARD_TYPE_HENSUN_CAM_PILOT_V1", kconfig)
        self.assertIn("elseif(CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1)", cmake)
        self.assertIn('set(BOARD_DIR "hensun/hensun-cam-pilot-v1")', cmake)

    def test_has_original_36_state_face_showcase_without_bitmap_assets(self):
        states = [
            "kReady",
            "kIdle",
            "kListening",
            "kThinking",
            "kSpeaking",
            "kInterrupted",
            "kHappy",
            "kCurious",
            "kCaring",
            "kReminder",
            "kNetworkError",
            "kSleep",
            "kLaughing",
            "kFunny",
            "kLoving",
            "kEmbarrassed",
            "kConfident",
            "kDelicious",
            "kSad",
            "kCrying",
            "kSleepy",
            "kSilly",
            "kAngry",
            "kSurprised",
            "kShocked",
            "kWinking",
            "kRelaxed",
            "kConfused",
            "kProud",
            "kExcited",
            "kWorried",
            "kApology",
            "kPairing",
            "kNetworkOk",
            "kUpdating",
            "kSafeBlock",
        ]
        for state in states:
            self.assertIn(state, self.face_header)
        self.assertIn("HensunFaceDisplay", self.source)
        self.assertIn("StartShowcase", self.source)
        self.assertIn("OnLongPress", self.source)
        self.assertIn("kAnimationPeriodMs = 50", self.face_source)
        self.assertIn("Original 36-state face set", self.face_source)
        self.assertIn("Starting 36-state display showcase", self.face_source)
        self.assertIn("LVGL face update avg=", self.face_source)
        self.assertIn("lv_obj_create", self.face_source)
        self.assertNotRegex(self.face_source, r"\.(?:png|gif|jpg|jpeg)\b")

    def test_face_states_are_bound_to_real_event_inputs(self):
        canonical_inputs = [
            "ready",
            "idle",
            "listening",
            "thinking",
            "speaking",
            "interrupted",
            "happy",
            "curious",
            "caring",
            "reminder",
            "network_error",
            "sleep",
            "laughing",
            "funny",
            "loving",
            "embarrassed",
            "confident",
            "delicious",
            "sad",
            "crying",
            "sleepy",
            "silly",
            "angry",
            "surprised",
            "shocked",
            "winking",
            "relaxed",
            "confused",
            "proud",
            "excited",
            "worried",
            "apology",
            "pairing",
            "network_ok",
            "updating",
            "safe_block",
        ]
        for event in canonical_inputs:
            self.assertRegex(self.face_source, rf'\{{"{event}",\s*HensunFaceState::k')
            self.assertIn(f"`{event}`", self.event_contract)

        self.assertIn('display_->SetEmotion("interrupted")', self.source)
        self.assertIn("ShowNotification", self.face_header)
        self.assertIn("Lang::Strings::CONNECTED_TO", self.face_source)
        self.assertIn("Face route source=", self.face_source)
        self.assertIn('"alarm"', self.face_source)
        self.assertIn('"content_blocked"', self.face_source)
        self.assertIn('"cloud_download"', self.face_source)

    def test_face_layer_preserves_camera_preview_and_hides_battery_ui(self):
        self.assertIn("LcdDisplay::SetPreviewImage", self.face_source)
        self.assertIn("preview_active_", self.face_source)
        self.assertIn("lv_obj_add_flag(face_layer_", self.face_source)
        self.assertIn("lv_obj_add_flag(battery_label_", self.face_source)


if __name__ == "__main__":
    unittest.main()
