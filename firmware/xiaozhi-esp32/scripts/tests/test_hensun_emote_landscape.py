import hashlib
import json
import struct
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"
ASSET_ROOT = BOARD / "emote_landscape"
SOURCE_ROOT = ASSET_ROOT / "source"
GIF_ROOT = ASSET_ROOT / "gifs"

EXPECTED = {
    "idle": (48, 6, 42),
    "listening": (28, 5, 23),
    "thinking": (36, 6, 30),
    "speaking": (20, 4, 16),
    "speaking_0": (20, 4, 16),
    "speaking_1": (20, 4, 16),
    "speaking_3": (20, 4, 16),
    "happy": (25, 8, 20),
    "caring": (40, 9, 33),
}


class HensunEmoteLandscapeTests(unittest.TestCase):
    def test_landscape_source_uses_the_approved_native_layout(self):
        spec = json.loads(
            (SOURCE_ROOT / "hensun_emote_motion_spec.json").read_text(encoding="utf-8")
        )
        self.assertEqual(spec["asset_set"], "hensun-emote-landscape-v1")
        self.assertEqual(spec["canvas"], {"width": 320, "height": 240, "fps": 20})
        self.assertEqual(spec["layout"]["left_eye_center"], [104, 105])
        self.assertEqual(spec["layout"]["right_eye_center"], [216, 105])
        self.assertEqual(spec["layout"]["mouth_center"], [160, 174])
        self.assertEqual(set(spec["animations"]), set(EXPECTED))

    def test_generated_landscape_assets_preserve_timing_palette_and_hashes(self):
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["asset_set"], "hensun-emote-landscape-v1")
        self.assertEqual(manifest["canvas"], {"width": 320, "height": 240, "fps": 20})

        for name, (frames, loop_start, loop_end) in EXPECTED.items():
            item = manifest["animations"][name]
            gif_path = GIF_ROOT / item["file"]
            self.assertEqual(item["frames"], frames)
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
                    colors = set(image.convert("RGB").get_flattened_data())
                    self.assertLessEqual(len(colors), 32)
                    self.assertTrue(
                        all(max(r, g, b) - min(r, g, b) <= 7 for r, g, b in colors)
                    )
                self.assertEqual(set(durations), {50})

    def test_landscape_pack_is_hash_locked_and_fits_the_existing_partition(self):
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))
        pack = ASSET_ROOT / manifest["pack"]["file"]
        self.assertLess(pack.stat().st_size, 5 * 1024 * 1024)
        self.assertEqual(manifest["pack"]["sha256"], hashlib.sha256(pack.read_bytes()).hexdigest())
        self.assertEqual(manifest["pack"]["animation_count"], 9)
        self.assertEqual(manifest["pack"]["asset_count"], 10)
        asset_count, stored_checksum, payload_length = struct.unpack_from("<III", pack.read_bytes())
        payload = pack.read_bytes()[12:]
        self.assertEqual(asset_count, 10)
        self.assertEqual(payload_length, len(payload))
        self.assertEqual(stored_checksum, sum(payload) & 0xFFFF)

if __name__ == "__main__":
    unittest.main()
