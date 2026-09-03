import hashlib
import json
import re
import struct
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"
ASSET_ROOT = BOARD / "emote_lab"
SOURCE_ROOT = ASSET_ROOT / "source"
GIF_ROOT = ASSET_ROOT / "gifs"
PARTITION = ROOT / "partitions/v2/16m_hensun_emote_lab.csv"

EXPECTED = {
    "wake": (24, 5, 19),
    "idle": (48, 6, 42),
    "listening": (28, 5, 23),
    "thinking": (36, 6, 30),
    "speaking": (20, 4, 16),
    "happy": (25, 8, 20),
    "caring": (40, 9, 33),
    "curious": (34, 7, 27),
    "surprised": (28, 6, 22),
    "confused": (36, 7, 29),
    "alert": (26, 5, 20),
    "sleep": (60, 8, 55),
}
SPEAKING_VARIANTS = {
    "speaking_0": (20, 4, 16),
    "speaking_1": (20, 4, 16),
    "speaking_3": (20, 4, 16),
}
ALL_EXPECTED = EXPECTED | SPEAKING_VARIANTS


class HensunEmoteLabTests(unittest.TestCase):
    def test_source_spec_defines_landscape_three_stage_animations(self):
        spec = json.loads(
            (SOURCE_ROOT / "hensun_emote_motion_spec.json").read_text(encoding="utf-8")
        )
        self.assertEqual(spec["asset_set"], "hensun-emote-landscape-v1")
        self.assertEqual(spec["canvas"], {"width": 320, "height": 240, "fps": 20})
        self.assertEqual(spec["palette"]["background"], "#000000")
        self.assertEqual(spec["palette"]["face"], "#F7F7F2")
        self.assertEqual(set(spec["animations"]), set(ALL_EXPECTED))

        signatures = set()
        for name, (frames, loop_start, loop_end) in EXPECTED.items():
            animation = spec["animations"][name]
            self.assertEqual(animation["frames"], frames)
            self.assertEqual(animation["loop_start_frame"], loop_start)
            self.assertEqual(animation["loop_end_frame"], loop_end)
            self.assertGreater(loop_start, 0)
            self.assertLess(loop_start, loop_end)
            self.assertLess(loop_end, frames)
            signatures.add(animation["silhouette"])
        self.assertEqual(len(signatures), len(EXPECTED))
        for name, level in (("speaking_0", 0), ("speaking_1", 1), ("speaking_3", 3)):
            animation = spec["animations"][name]
            self.assertEqual(animation["renderer"], "speaking")
            self.assertEqual(animation["speech_level"], level)

    def test_generated_gifs_match_screen_fps_duration_and_palette(self):
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["asset_set"], "hensun-emote-landscape-v1")
        self.assertEqual(manifest["canvas"], {"width": 320, "height": 240, "fps": 20})
        self.assertEqual(len(manifest["animations"]), len(ALL_EXPECTED))

        for name, (frames, loop_start, loop_end) in ALL_EXPECTED.items():
            item = manifest["animations"][name]
            gif_path = GIF_ROOT / item["file"]
            self.assertTrue(gif_path.is_file(), gif_path)
            self.assertRegex(
                item["file"],
                rf"^{name}_\[[0-9.]+s,[0-9.]+s\]\.gif$",
            )
            self.assertEqual(item["loop_start_frame"], loop_start)
            self.assertEqual(item["loop_end_frame"], loop_end)
            self.assertEqual(item["sha256"], hashlib.sha256(gif_path.read_bytes()).hexdigest())

            with Image.open(gif_path) as image:
                self.assertEqual(image.size, (320, 240))
                self.assertEqual(image.n_frames, frames)
                durations = []
                for frame_index in range(image.n_frames):
                    image.seek(frame_index)
                    durations.append(image.info.get("duration"))
                    rgb = image.convert("RGB")
                    colors = set(rgb.get_flattened_data())
                    self.assertLessEqual(len(colors), 32)
                    self.assertTrue(all(max(r, g, b) - min(r, g, b) <= 7 for r, g, b in colors))
                self.assertEqual(set(durations), {50})

    def test_pack_is_hash_locked_and_fits_partition(self):
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        pack = ASSET_ROOT / manifest["pack"]["file"]
        self.assertTrue(pack.is_file(), pack)
        self.assertLess(pack.stat().st_size, 5 * 1024 * 1024)
        self.assertEqual(manifest["pack"]["sha256"], hashlib.sha256(pack.read_bytes()).hexdigest())
        self.assertEqual(manifest["pack"]["animation_count"], 18)
        self.assertEqual(manifest["pack"]["asset_count"], 19)

        pack_data = pack.read_bytes()
        asset_count, stored_checksum, payload_length = struct.unpack_from("<III", pack_data)
        payload = pack_data[12:]
        self.assertEqual(asset_count, manifest["pack"]["asset_count"])
        self.assertEqual(payload_length, len(payload))
        self.assertEqual(stored_checksum, sum(payload) & 0xFFFF)

        entry_size = 16 + 12
        names = []
        for index in range(asset_count):
            raw_name = pack_data[12 + index * entry_size : 12 + index * entry_size + 16]
            self.assertIn(0, raw_name, "mmap asset names require a terminating NUL byte")
            names.append(raw_name.split(b"\0", 1)[0].decode("ascii"))
        self.assertIn("talk_base.eaf", names)
        self.assertNotIn("talk_neutral.eaf", names)

    def test_lab_variant_uses_isolated_partition_and_player_dependency(self):
        config = json.loads((BOARD / "config.json").read_text(encoding="utf-8"))
        builds = {build["name"]: build for build in config["builds"]}
        self.assertIn("hensun-cam-emote-lab-v1", builds)
        lab_sdkconfig = "\n".join(builds["hensun-cam-emote-lab-v1"]["sdkconfig_append"])
        self.assertIn("CONFIG_USE_EMOTE_MESSAGE_STYLE=y", lab_sdkconfig)
        self.assertIn(
            'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions/v2/16m_hensun_emote_lab.csv"',
            lab_sdkconfig,
        )
        self.assertIn("api.hensun.invalid/v1/device/xiaozhi-bootstrap", lab_sdkconfig)

        partition = PARTITION.read_text(encoding="utf-8")
        self.assertRegex(partition, r"(?m)^nvs,\s+data,\s+nvs,\s+0x9000,\s+0x4000,")
        self.assertRegex(partition, r"(?m)^ota_0,\s+app,\s+ota_0,\s+0x20000,\s+0x3f0000,")
        self.assertRegex(partition, r"(?m)^hensun_keys,\s+data,\s+nvs,\s+0x800000,\s+0x4000")
        self.assertRegex(partition, r"(?m)^model,\s+data,\s+spiffs,\s+0x804000,\s+0x2FC000")
        self.assertRegex(partition, r"(?m)^emote_gen,\s+data,\s+spiffs,\s+0xB00000,\s+5M")

        component_manifest = (ROOT / "main/idf_component.yml").read_text(encoding="utf-8")
        self.assertIn("https://github.com/espressif2022/esp_emote_gen_player.git", component_manifest)
        self.assertIn("7139b46c6616d466ff153cb9d2ddf63661434f22", component_manifest)

    def test_display_routes_landscape_states_through_a_worker_queue(self):
        header = (BOARD / "hensun_emote_lab_display.h").read_text(encoding="utf-8")
        source = (BOARD / "hensun_emote_lab_display.cc").read_text(encoding="utf-8")
        board = (BOARD / "hensun_cam_pilot_v1_board.cc").read_text(encoding="utf-8")
        kconfig = (ROOT / "main/Kconfig.projbuild").read_text(encoding="utf-8")

        self.assertIn("class HensunEmoteLabDisplay", header)
        self.assertIn("xQueueCreate", source)
        self.assertIn("emote_gen_player_anim_fade_name", source)
        self.assertIn("emote_gen_player_anim_now_name", source)
        for animation in EXPECTED:
            self.assertRegex(source, rf'"{re.escape(animation)}"')
        for alias in (
            "idle_entered",
            "listening_started",
            "processing_started",
            "query_result_ready",
            "positive_response",
            "comfort_mode_entered",
        ):
            self.assertIn(alias, source)
        self.assertIn("unknown emotion", source)
        self.assertIn("CONFIG_USE_EMOTE_MESSAGE_STYLE", board)
        self.assertIn("new HensunEmoteLabDisplay", board)
        self.assertRegex(
            board,
            r"OnLongPress\(\[this\]\(\) \{\s*"
            r'ESP_LOGI\(TAG, "BOOT long press: entering Wi-Fi configuration"\);\s*'
            r"EnterWifiConfigMode\(\);",
        )
        self.assertRegex(
            board,
            r"OnDoubleClick\(\[this\]\(\) \{\s*display_->StartShowcase\(\);",
        )
        self.assertIn("BOARD_TYPE_HENSUN_CAM_PILOT_V1", kconfig)
        self.assertIn("config USE_EMOTE_MESSAGE_STYLE", kconfig)

    def test_camera_is_available_on_demand_and_lab_assets_flash_with_build(self):
        board = (BOARD / "hensun_cam_pilot_v1_board.cc").read_text(encoding="utf-8")
        cmake = (ROOT / "main/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("InitializeCamera();", board)
        self.assertIn("class HensunLazyCamera final : public Camera", board)
        self.assertIn("std::make_unique<Esp32Camera>(config_)", board)
        self.assertIn("return EnsureCamera() && camera_->Capture();", board)
        self.assertIn("spiffs_create_partition_assets", cmake)
        self.assertIn("hensun_emote_lab_v1.bin", cmake)
        self.assertIn("FLASH_IN_PROJECT", cmake)


if __name__ == "__main__":
    unittest.main()
