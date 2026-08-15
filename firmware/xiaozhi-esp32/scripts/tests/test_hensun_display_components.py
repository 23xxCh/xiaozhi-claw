import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "main/boards/hensun/hensun-cam-pilot-v1"


def source(name: str) -> str:
    return (BOARD / name).read_text(encoding="utf-8")


class HensunDisplayComponentTests(unittest.TestCase):
    def test_panel_owns_only_lcd_transport_and_preview_flush(self):
        panel = source("hensun_panel.cc")
        self.assertIn("spi_bus_initialize", panel)
        self.assertIn("esp_lcd_new_panel_st7789", panel)
        self.assertIn("HensunPanel::DrawPreview", panel)
        self.assertNotIn("emote_gen_player_mount_assets", panel)
        self.assertNotIn("RotateRgb565Clockwise", panel)

    def test_panel_recovers_a_stalled_animation_flush(self):
        panel = source("hensun_panel.cc")
        header = source("hensun_panel.h")
        self.assertIn("kAnimationFlushTimeoutMs", panel)
        self.assertIn("AnimationFlushWatchdog", panel)
        self.assertIn("animation flush timed out", panel)
        self.assertIn("animation_flush_started_us_", header)

    def test_renderer_owns_assets_queue_and_animation_switching(self):
        renderer = source("emote_renderer.cc")
        for marker in (
            "emote_gen_player_mount_assets",
            "xQueueCreate",
            "emote_gen_player_anim_fade_name",
            "emote_gen_player_anim_now_name",
        ):
            self.assertIn(marker, renderer)
        self.assertNotIn("spi_bus_initialize", renderer)

    def test_mapper_is_independent_of_panel_and_renderer(self):
        mapper = source("emotion_mapper.cc")
        self.assertIn("EmotionMapper::Map", mapper)
        self.assertIn("unknown emotion", mapper)
        self.assertNotIn("esp_lcd", mapper)
        self.assertNotIn("emote_gen_player", mapper)

    def test_speech_envelope_owns_pcm_thresholds_and_hysteresis(self):
        header = source("speech_envelope.h")
        implementation = source("speech_envelope.cc")
        self.assertIn("noise_floor", header)
        self.assertIn("reference_amplitude", header)
        self.assertIn("SpeechEnvelope::MeasureLevel", implementation)
        self.assertIn("SpeechEnvelope::Quantize", implementation)
        self.assertNotIn("AudioCodec::OutputData", implementation)

    def test_camera_preview_owns_rotation_and_psram_frame(self):
        preview = source("camera_preview.cc")
        self.assertIn("RotateRgb565Clockwise", preview)
        self.assertIn("MALLOC_CAP_SPIRAM", preview)
        self.assertIn("panel_.DrawPreview", preview)

    def test_facade_and_board_only_coordinate_components(self):
        facade = source("hensun_emote_lab_display.cc")
        board = source("hensun_cam_pilot_v1_board.cc")
        self.assertLess(len(facade.splitlines()), 180)
        self.assertIn("renderer_.Queue", facade)
        self.assertIn("preview_.Show", facade)
        self.assertIn("new HensunPanel", board)
        self.assertNotIn("spi_bus_initialize", board)
        self.assertNotIn("RotateRgb565Clockwise", board)
        self.assertNotIn("noise_floor", board)


if __name__ == "__main__":
    unittest.main()
