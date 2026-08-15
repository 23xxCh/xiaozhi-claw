import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"
sys.path.insert(0, str(ROOT / "scripts"))
from profile_codegen import render_profile_bundle  # noqa: E402


class HensunProfileTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((BOARD / "config.json").read_text(encoding="utf-8"))

    def test_all_variants_use_only_versioned_profile_bundles(self):
        for build in self.config["builds"]:
            self.assertEqual(set(build["profile_bundle"]), {"hardware", "display", "product"})
            self.assertNotIn("sdkconfig_append", build)
            rendered = render_profile_bundle(ROOT, BOARD, build)
            self.assertIn("HENSUN_HARDWARE_PROFILE_ID", rendered.header)
            self.assertIn("HENSUN_DISPLAY_PROFILE_ID", rendered.header)
            self.assertIn("HENSUN_PRODUCT_VARIANT_ID", rendered.header)
            self.assertIn("HENSUN_PROFILE_SHA256", rendered.header)

    def test_generator_check_detects_stale_output(self):
        build = next(
            item for item in self.config["builds"] if item["name"] == "hensun-cam-selfhosted-v1"
        )
        rendered = render_profile_bundle(ROOT, BOARD, build)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            rendered.write(output)
            rendered.write(output, check=True)
            (output / "hensun_profile.cmake").write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "stale"):
                rendered.write(output, check=True)

    def test_invalid_display_geometry_fails_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board"
            shutil.copytree(BOARD / "profiles", board / "profiles")
            shutil.copytree(BOARD / "emote_landscape", board / "emote_landscape")
            display_path = board / "profiles/display/st7789-landscape-v1.json"
            display = json.loads(display_path.read_text(encoding="utf-8"))
            display["logical"]["width"] = 319
            display_path.write_text(json.dumps(display), encoding="utf-8")
            build = next(
                item for item in self.config["builds"] if item["name"] == "hensun-cam-selfhosted-v1"
            )
            with self.assertRaisesRegex(ValueError, "logical size"):
                render_profile_bundle(ROOT, board, build)

    def test_pin_conflicts_fail_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "board"
            shutil.copytree(BOARD / "profiles", board / "profiles")
            shutil.copytree(BOARD / "emote_landscape", board / "emote_landscape")
            hardware_path = board / "profiles/hardware/hensun-cam-pilot-v1.json"
            hardware = json.loads(hardware_path.read_text(encoding="utf-8"))
            hardware["pins"]["display"]["mosi"] = hardware["pins"]["audio"]["mic_ws"]
            hardware_path.write_text(json.dumps(hardware), encoding="utf-8")
            build = next(
                item for item in self.config["builds"] if item["name"] == "hensun-cam-selfhosted-v1"
            )
            with self.assertRaisesRegex(ValueError, "conflicts"):
                render_profile_bundle(ROOT, board, build)


if __name__ == "__main__":
    unittest.main()
