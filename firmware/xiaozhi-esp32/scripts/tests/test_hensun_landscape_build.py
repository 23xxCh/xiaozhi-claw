import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parents[1]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"
sys.path.insert(0, str(ROOT / "scripts"))
from profile_codegen import render_profile_bundle  # noqa: E402


class HensunLandscapeBuildTests(unittest.TestCase):
    def setUp(self):
        config = json.loads((BOARD / "config.json").read_text(encoding="utf-8"))
        self.builds = {}
        for build in config["builds"]:
            rendered = render_profile_bundle(ROOT, BOARD, build)
            self.builds[build["name"]] = {
                **build,
                "sdkconfig_append": list(rendered.sdkconfig),
            }

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
        self.assertIn('#include "hensun_profile_generated.h"', header)
        landscape = json.loads(
            (BOARD / "profiles/display/st7789-landscape-v1.json").read_text(
                encoding="utf-8"
            )
        )
        portrait = json.loads(
            (BOARD / "profiles/display/st7789-portrait-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(landscape["logical"], {"width": 320, "height": 240})
        self.assertEqual(
            landscape["transform"],
            {"swap_xy": True, "mirror_x": True, "mirror_y": False},
        )
        self.assertEqual(portrait["logical"], {"width": 240, "height": 320})

        kconfig = (ROOT / "main/Kconfig.projbuild").read_text(encoding="utf-8")
        self.assertIn("config HENSUN_DISPLAY_LANDSCAPE", kconfig)
        self.assertIn("depends on BOARD_TYPE_HENSUN_CAM_PILOT_V1", kconfig)

    def test_landscape_build_selects_its_own_pack(self):
        cmake = (ROOT / "main/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("hensun_profile.cmake", cmake)
        self.assertIn("HENSUN_EMOTE_ASSET_DIR", cmake)
        self.assertIn("HENSUN_EMOTE_ASSET_BIN", cmake)
        self.assertNotIn("set(HENSUN_EMOTE_LAB_DIR", cmake)

    def test_build_flash_and_release_gate_expose_landscape_variant(self):
        build_script = (REPO_ROOT / "scripts/build_firmware.ps1").read_text(encoding="utf-8")
        flash_script = (REPO_ROOT / "scripts/flash_firmware.ps1").read_text(encoding="utf-8")
        release_gate = (REPO_ROOT / "scripts/release_gate.py").read_text(encoding="utf-8")
        self.assertIn('"selfhosted-landscape"', build_script)
        self.assertIn('"selfhosted-portrait"', build_script)
        self.assertIn('"hensun-cam-selfhosted-landscape-v1"', build_script)
        self.assertIn('"hensun-cam-selfhosted-portrait-v1"', build_script)
        self.assertIn("HENSUN_BOOTSTRAP_URL", build_script)
        self.assertNotIn("managed_components", build_script)
        self.assertNotIn("config.hensun-build", build_script)
        self.assertIn('"selfhosted-landscape"', flash_script)
        self.assertIn('"selfhosted-portrait"', flash_script)
        self.assertIn('$result.EmoteAssetsPath', flash_script)
        self.assertIn('"hensun-cam-selfhosted-landscape-v1"', release_gate)
        self.assertIn('"hensun-cam-selfhosted-portrait-v1"', release_gate)
        self.assertIn("landscape CAM firmware", release_gate)
        self.assertIn("portrait CAM firmware", release_gate)

    def test_landscape_camera_preview_is_native_qvga_and_portrait_still_rotates(self):
        preview = (BOARD / "camera_preview.cc").read_text(encoding="utf-8")
        board = (BOARD / "hensun_cam_pilot_v1_board.cc").read_text(encoding="utf-8")
        self.assertIn("width == width_ && height == height_", preview)
        self.assertIn("width == height_ && height == width_", preview)
        self.assertIn("RotateRgb565Clockwise", preview)
        self.assertIn("camera_config.frame_size = FRAMESIZE_QVGA", board)


if __name__ == "__main__":
    unittest.main()
