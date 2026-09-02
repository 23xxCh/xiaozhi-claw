import csv
import json
import struct
import unittest
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/hensun/hensun-nocam-pilot-v1"
PARTITION = ROOT / "partitions/v2/16m_hensun_nocam.csv"
OTA_SOURCE = ROOT / "main/ota.cc"
EMOTES = BOARD / "standard_emotes"


class HensunNoCamPilotBoardTests(unittest.TestCase):
    def read_required(self, path: Path) -> str:
        self.assertTrue(path.is_file(), f"missing board file: {path}")
        return path.read_text(encoding="utf-8")

    def test_has_isolated_selfhosted_build_identity(self):
        config = json.loads(self.read_required(BOARD / "config.json"))

        self.assertEqual(config["manufacturer"], "hensun")
        self.assertEqual(config["type"], "hensun-nocam-pilot-v1")
        self.assertEqual(
            [build["name"] for build in config["builds"]],
            ["hensun-nocam-selfhosted-v1"],
        )

        sdkconfig = "\n".join(config["builds"][0]["sdkconfig_append"])
        self.assertIn("CONFIG_LCD_ST7789_240X320=y", sdkconfig)
        self.assertIn("CONFIG_USE_HOTSPOT_WIFI_PROVISIONING=y", sdkconfig)
        self.assertIn("CONFIG_USE_ESP_BLUFI_WIFI_PROVISIONING=n", sdkconfig)
        self.assertIn("CONFIG_SEND_WAKE_WORD_DATA=n", sdkconfig)
        self.assertIn("CONFIG_USE_DEVICE_AEC=n", sdkconfig)
        self.assertIn("CONFIG_PARTITION_TABLE_CUSTOM=y", sdkconfig)
        self.assertIn(
            'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions/v2/16m_hensun_nocam.csv"',
            sdkconfig,
        )
        self.assertIn("https://api.hensun.invalid/v1/ota/", sdkconfig)

    def test_reserves_identity_without_overlapping_assets(self):
        partition = self.read_required(PARTITION)

        self.assertRegex(
            partition,
            r"hensun_keys,\s*data,\s*nvs,\s*0x800000,\s*0x4000,",
        )
        self.assertRegex(
            partition,
            r"assets,\s*data,\s*spiffs,\s*0x804000,\s*0x7FC000,",
        )

        rows = list(
            csv.reader(
                StringIO(
                    "\n".join(
                        line for line in partition.splitlines() if not line.startswith("#")
                    )
                )
            )
        )
        layout = {}
        for row in rows:
            self.assertGreaterEqual(len(row), 5)
            name, offset, size = row[0].strip(), row[3].strip(), row[4].strip()
            self.assertTrue(offset, f"{name} requires an explicit offset")
            self.assertTrue(size, f"{name} requires an explicit size")
            layout[name] = (int(offset, 0), int(size, 0))

        ordered = sorted(layout.items(), key=lambda item: item[1][0])
        for (_, (offset, size)), (_, (next_offset, _)) in zip(ordered, ordered[1:]):
            self.assertLessEqual(offset + size, next_offset)
        self.assertEqual(ordered[-1][1][0] + ordered[-1][1][1], 0x1000000)

    def test_ota_auth_reads_nocam_identity_partition(self):
        ota_source = self.read_required(OTA_SOURCE)

        credential_guard = (
            "CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 || "
            "CONFIG_BOARD_TYPE_HENSUN_DESK_V1 || "
            "CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1"
        )
        self.assertEqual(ota_source.count(credential_guard), 2)

    def test_matches_xz_ai_kzb_v17_v18_audio_display_and_button_pinout(self):
        pins = self.read_required(BOARD / "config.h")
        expected_defines = {
            "AUDIO_I2S_MIC_GPIO_WS": "GPIO_NUM_4",
            "AUDIO_I2S_MIC_GPIO_SCK": "GPIO_NUM_5",
            "AUDIO_I2S_MIC_GPIO_DIN": "GPIO_NUM_6",
            "AUDIO_I2S_SPK_GPIO_DOUT": "GPIO_NUM_7",
            "AUDIO_I2S_SPK_GPIO_BCLK": "GPIO_NUM_15",
            "AUDIO_I2S_SPK_GPIO_LRCK": "GPIO_NUM_16",
            "BOOT_BUTTON_GPIO": "GPIO_NUM_0",
            "VOLUME_UP_BUTTON_GPIO": "GPIO_NUM_38",
            "VOLUME_DOWN_BUTTON_GPIO": "GPIO_NUM_39",
            "DISPLAY_BACKLIGHT_PIN": "GPIO_NUM_42",
            "DISPLAY_CS_PIN": "GPIO_NUM_41",
            "DISPLAY_DC_PIN": "GPIO_NUM_40",
            "DISPLAY_RST_PIN": "GPIO_NUM_45",
            "DISPLAY_MOSI_PIN": "GPIO_NUM_47",
            "DISPLAY_CLK_PIN": "GPIO_NUM_21",
        }
        for name, value in expected_defines.items():
            self.assertRegex(pins, rf"#define\s+{name}\s+{value}\b")

        self.assertIn("#define AUDIO_I2S_METHOD_SIMPLEX", pins)
        self.assertIn("#define LCD_TYPE_ST7789_SERIAL", pins)
        self.assertRegex(pins, r"#define\s+DISPLAY_WIDTH\s+240\b")
        self.assertRegex(pins, r"#define\s+DISPLAY_HEIGHT\s+320\b")

    def test_uses_simplex_audio_and_has_no_camera_runtime(self):
        source = self.read_required(BOARD / "hensun_nocam_pilot_v1_board.cc")

        self.assertIn("NoAudioCodecSimplex", source)
        self.assertIn("volume_up_button_", source)
        self.assertIn("volume_down_button_", source)
        self.assertIn("EnterWifiConfigMode();", source)
        self.assertNotIn("Camera", source)
        self.assertNotIn("LAMP_GPIO", source)

    def test_uses_board_specific_standard_emoji_collection(self):
        cmake = self.read_required(ROOT / "main/CMakeLists.txt")

        nocam_block = cmake.split(
            "elseif(CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1)", 1
        )[1].split("elseif(", 1)[0]
        self.assertIn(
            'set(DEFAULT_EMOJI_COLLECTION "${CMAKE_CURRENT_SOURCE_DIR}/boards/${BOARD_DIR}/standard_emotes")',
            nocam_block,
        )
        self.assertIn('if(IS_ABSOLUTE "${DEFAULT_EMOJI_COLLECTION}")', cmake)
        self.assertIn(
            'file(GLOB DEFAULT_EMOJI_FILES CONFIGURE_DEPENDS "${DEFAULT_EMOJI_COLLECTION}/*")',
            cmake,
        )

    def test_standard_emotes_are_animated_240_by_180_gifs(self):
        expected = {
            "neutral.gif",
            "shy.gif",
            "sad.gif",
            "angry.gif",
            "surprised.gif",
            "sleepy.gif",
            "confused.gif",
            "caring.gif",
            "silly.gif",
        }
        actual = {path.name for path in EMOTES.glob("*.gif")}
        self.assertEqual(actual, expected)

        for path in EMOTES.glob("*.gif"):
            payload = path.read_bytes()
            self.assertIn(payload[:6], {b"GIF87a", b"GIF89a"})
            self.assertGreater(payload.count(b"\x2c"), 1, path.name)
            self.assertEqual(struct.unpack("<HH", payload[6:10]), (240, 180))

    def test_maps_cloud_emotions_to_the_nine_standard_assets(self):
        source = self.read_required(BOARD / "hensun_nocam_pilot_v1_board.cc")

        self.assertIn("class HensunNoCamDisplay", source)
        expected_routes = {
            '"neutral", "neutral"',
            '"happy", "silly"',
            '"laughing", "silly"',
            '"caring", "caring"',
            '"affectionate", "caring"',
            '"curious", "confused"',
            '"surprised", "surprised"',
            '"confused", "confused"',
            '"concerned", "sad"',
            '"apologetic", "sad"',
            '"shy", "shy"',
            '"sad", "sad"',
        }
        for route in expected_routes:
            self.assertIn(route, source)
        self.assertIn('return "neutral";', source)

    def test_is_registered_as_an_esp32s3_board(self):
        kconfig = self.read_required(ROOT / "main/Kconfig.projbuild")
        cmake = self.read_required(ROOT / "main/CMakeLists.txt")

        self.assertIn("config BOARD_TYPE_HENSUN_NOCAM_PILOT_V1", kconfig)
        self.assertIn("depends on IDF_TARGET_ESP32S3", kconfig)
        self.assertIn("elseif(CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1)", cmake)
        self.assertIn('set(BOARD_DIR "hensun/hensun-nocam-pilot-v1")', cmake)


if __name__ == "__main__":
    unittest.main()
