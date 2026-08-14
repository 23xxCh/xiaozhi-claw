import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parents[1]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"


class HensunLandscapeBuildTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((BOARD / "config.json").read_text(encoding="utf-8"))
        self.builds = {build["name"]: build for build in config["builds"]}

    def test_selfhosted_defaults_to_landscape_and_portrait_remains_available(self):
        default = set(self.builds["hensun-cam-selfhosted-v1"]["sdkconfig_append"])
        landscape_alias = set(
            self.builds["hensun-cam-selfhosted-landscape-v1"]["sdkconfig_append"]
        )
        portrait = set(
            self.builds["hensun-cam-selfhosted-portrait-v1"]["sdkconfig_append"]
        )
        self.assertIn("CONFIG_HENSUN_DISPLAY_LANDSCAPE=y", default)
        self.assertEqual(landscape_alias, default)
        self.assertEqual(
            default - {"CONFIG_HENSUN_DISPLAY_LANDSCAPE=y"},
            portrait,
        )
        self.assertNotIn(
            "CONFIG_HENSUN_DISPLAY_LANDSCAPE=y",
            self.builds["hensun-cam-official-v1"]["sdkconfig_append"],
        )

    def test_landscape_display_geometry_matches_counterclockwise_mount(self):
        header = (BOARD / "config.h").read_text(encoding="utf-8")
        self.assertIn("CONFIG_HENSUN_DISPLAY_LANDSCAPE", header)
        for text in (
            "#define DISPLAY_WIDTH   320",
            "#define DISPLAY_HEIGHT  240",
            "#define DISPLAY_MIRROR_X true",
            "#define DISPLAY_MIRROR_Y false",
            "#define DISPLAY_SWAP_XY true",
        ):
            self.assertIn(text, header)
        for portrait_text in (
            "#define DISPLAY_WIDTH   240",
            "#define DISPLAY_HEIGHT  320",
            "#define DISPLAY_MIRROR_X false",
            "#define DISPLAY_MIRROR_Y false",
            "#define DISPLAY_SWAP_XY false",
        ):
            self.assertIn(portrait_text, header)

        kconfig = (ROOT / "main/Kconfig.projbuild").read_text(encoding="utf-8")
        self.assertIn("config HENSUN_DISPLAY_LANDSCAPE", kconfig)
        self.assertIn("depends on BOARD_TYPE_HENSUN_CAM_PILOT_V1", kconfig)

    def test_landscape_build_selects_its_own_pack(self):
        cmake = (ROOT / "main/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("CONFIG_HENSUN_DISPLAY_LANDSCAPE", cmake)
        self.assertIn("emote_landscape", cmake)
        self.assertIn("hensun_emote_landscape_v1.bin", cmake)
        self.assertIn("hensun_emote_lab_v1.bin", cmake)

    def test_build_flash_and_release_gate_expose_landscape_variant(self):
        build_script = (REPO_ROOT / "scripts/build_firmware.ps1").read_text(encoding="utf-8")
        flash_script = (REPO_ROOT / "scripts/flash_firmware.ps1").read_text(encoding="utf-8")
        release_gate = (REPO_ROOT / "scripts/release_gate.py").read_text(encoding="utf-8")
        self.assertIn('"selfhosted-landscape"', build_script)
        self.assertIn('"selfhosted-portrait"', build_script)
        self.assertIn('"hensun-cam-selfhosted-landscape-v1"', build_script)
        self.assertIn('"hensun-cam-selfhosted-portrait-v1"', build_script)
        self.assertIn("Repair-WhitespaceUnsafeComponentLinkFlags", build_script)
        self.assertIn('"-L${CMAKE_CURRENT_SOURCE_DIR}', build_script)
        self.assertIn('"selfhosted-landscape"', flash_script)
        self.assertIn('"selfhosted-portrait"', flash_script)
        self.assertIn('$Variant -in @("selfhosted", "selfhosted-landscape")', flash_script)
        self.assertIn('"hensun-cam-selfhosted-landscape-v1"', release_gate)
        self.assertIn('"hensun-cam-selfhosted-portrait-v1"', release_gate)
        self.assertIn("landscape CAM firmware", release_gate)
        self.assertIn("portrait CAM firmware", release_gate)

    def test_landscape_camera_preview_is_native_qvga_and_portrait_still_rotates(self):
        display = (BOARD / "hensun_emote_lab_display.cc").read_text(encoding="utf-8")
        board = (BOARD / "hensun_cam_pilot_v1_board.cc").read_text(encoding="utf-8")
        self.assertIn("width == width_ && height == height_", display)
        self.assertIn("width == height_ && height == width_", display)
        self.assertIn("RotateRgb565Clockwise", display)
        self.assertIn("camera_config.frame_size = FRAMESIZE_QVGA", board)


if __name__ == "__main__":
    unittest.main()
