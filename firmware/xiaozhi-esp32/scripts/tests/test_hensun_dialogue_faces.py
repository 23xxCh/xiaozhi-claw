import hashlib
import json
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"
ASSET_ROOT = BOARD / "emote_lab/dialogue_preview"
RUNTIME_ROOT = BOARD / "emote_lab/dialogue_runtime"
SOURCE_SPEC = BOARD / "emote_lab/source/hensun_dialogue_face_spec.json"
EXPECTED = {
    "neutral",
    "happy",
    "laughing",
    "caring",
    "affectionate",
    "curious",
    "surprised",
    "confused",
    "concerned",
    "apologetic",
}
DISPLAY_SOURCE = BOARD / "hensun_emote_lab_display.cc"
DISPLAY_HEADER = BOARD / "hensun_emote_lab_display.h"
MOUTH_SOURCE = BOARD / "hensun_speech_mouth_renderer.cc"
MOUTH_HEADER = BOARD / "hensun_speech_mouth_renderer.h"


class HensunDialogueFaceTests(unittest.TestCase):
    def test_spec_defines_ten_conversation_faces_and_five_mouth_poses(self):
        spec = json.loads(SOURCE_SPEC.read_text(encoding="utf-8"))

        self.assertEqual(spec["asset_set"], "hensun-dialogue-faces-v1")
        self.assertEqual(spec["canvas"], {"width": 320, "height": 240, "fps": 20})
        self.assertEqual(spec["palette"], {"background": "#000000", "face": "#F7F7F2"})
        self.assertEqual(set(spec["expressions"]), EXPECTED)
        self.assertEqual(
            spec["mouth"]["poses"],
            ["closed", "light", "narrow", "round", "wide"],
        )
        self.assertIn("single-stroke or filled micro-mouth", spec["mouth"]["rule"])
        for expression in spec["expressions"].values():
            self.assertEqual(expression["frames"], 32)
            self.assertEqual(expression["loop_start_frame"], 6)
            self.assertEqual(expression["loop_end_frame"], 26)

    def test_generated_dialogue_previews_are_hash_locked_and_grayscale(self):
        manifest = json.loads((ASSET_ROOT / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["asset_set"], "hensun-dialogue-faces-v1")
        self.assertEqual(set(manifest["expressions"]), EXPECTED)
        for item in manifest["expressions"].values():
            path = ASSET_ROOT / item["file"]
            self.assertTrue(path.is_file(), path)
            self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            with Image.open(path) as image:
                self.assertEqual(image.size, (320, 240))
                self.assertEqual(image.n_frames, 32)
                durations = []
                for frame_index in range(image.n_frames):
                    image.seek(frame_index)
                    durations.append(image.info.get("duration"))
                    colors = set(image.convert("RGB").get_flattened_data())
                    self.assertLessEqual(len(colors), 32)
                    self.assertTrue(all(max(rgb) - min(rgb) <= 7 for rgb in colors))
                self.assertEqual(set(durations), {50})

    def test_contact_sheets_cover_all_faces_and_mouths_stay_small_and_solid(self):
        with Image.open(ASSET_ROOT / "hensun_dialogue_faces_contact_sheet.png") as contact:
            self.assertEqual(contact.size, (1600, 536))
        with Image.open(ASSET_ROOT / "hensun_natural_mouth_5pose.png") as mouth_source:
            mouths = mouth_source.convert("RGB")
            self.assertEqual(mouths.size, (1600, 268))
            widths = []
            heights = []
            for pose in range(5):
                tile_x = pose * 320
                crop = mouths.crop((tile_x + 100, 150, tile_x + 220, 190))
                bright = [
                    (x, y)
                    for y in range(crop.height)
                    for x in range(crop.width)
                    if max(crop.getpixel((x, y))) > 180
                ]
                self.assertTrue(bright, f"mouth pose {pose} must render")
                width = max(x for x, _ in bright) - min(x for x, _ in bright) + 1
                height = max(y for _, y in bright) - min(y for _, y in bright) + 1
                widths.append(width)
                heights.append(height)
                self.assertLessEqual(width, 52, f"mouth pose {pose} is too wide")
                self.assertLessEqual(height, 18, f"mouth pose {pose} is too tall")
            self.assertEqual(widths, sorted(widths), "mouth width must grow without shape jumps")
            self.assertEqual(heights, sorted(heights), "mouth depth must grow without shape jumps")

    def test_first_runtime_faces_are_eye_only_for_layered_mouth_rendering(self):
        manifest = json.loads((RUNTIME_ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["expressions"]), {"neutral", "happy", "caring"})
        for name, item in manifest["expressions"].items():
            self.assertEqual(item["loop_start_frame"], 0)
            self.assertEqual(item["loop_end_frame"], item["frames"])
            path = RUNTIME_ROOT / item["file"]
            self.assertTrue(path.is_file(), path)
            self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            with Image.open(path) as image:
                self.assertEqual(image.size, (320, 240))
                self.assertEqual(image.n_frames, 32)
                for frame_index in range(image.n_frames):
                    image.seek(frame_index)
                    frame = image.convert("RGB")
                    mouth_region = frame.crop((130, 155, 190, 188))
                    self.assertLessEqual(
                        max(max(pixel) for pixel in mouth_region.get_flattened_data()),
                        8,
                        f"{name} runtime face must leave the mouth layer empty",
                    )
                    eye_heights = []
                    for side, region in (
                        ("left", (55, 55, 150, 150)),
                        ("right", (170, 55, 265, 150)),
                    ):
                        eye = frame.crop(region)
                        bright = [
                            (x, y)
                            for y in range(eye.height)
                            for x in range(eye.width)
                            if max(eye.getpixel((x, y))) > 180
                        ]
                        self.assertTrue(bright, f"{name} {side} eye must remain visible")
                        eye_height = max(y for _, y in bright) - min(y for _, y in bright) + 1
                        eye_heights.append(eye_height)
                    self.assertLessEqual(
                        abs(eye_heights[0] - eye_heights[1]),
                        3,
                        f"{name} must never collapse only one eye during speech",
                    )

    def test_firmware_coalesces_stale_face_requests_and_does_not_restart_mid_reply(self):
        display = DISPLAY_SOURCE.read_text(encoding="utf-8")
        header = DISPLAY_HEADER.read_text(encoding="utf-8")

        self.assertIn("uint32_t generation;", header)
        self.assertIn("animation_generation_", header)
        self.assertIn("request.generation != animation_generation_.load()", display)
        self.assertNotIn(
            "else if (presentation_state_.load() == PresentationState::kSpeaking) {\n"
            "        QueueAnimation(ConversationAnimation(), false, true);\n"
            "    }",
            display,
        )

    def test_firmware_mouth_uses_one_smooth_curve_family(self):
        source = MOUTH_SOURCE.read_text(encoding="utf-8")
        header = MOUTH_HEADER.read_text(encoding="utf-8")

        self.assertIn("target_pose_", header)
        self.assertIn("render_pose_", header)
        self.assertIn("kPoseStepIntervalUs", header)
        self.assertIn("y_start == 0", source)
        self.assertNotIn("DrawFilledEllipse", source)


if __name__ == "__main__":
    unittest.main()
