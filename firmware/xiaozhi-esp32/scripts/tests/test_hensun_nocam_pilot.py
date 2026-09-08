import csv
import json
import re
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

    def test_nocam_advertises_implemented_playback_and_telemetry_without_pcm_mouth(self):
        protocol = self.read_required(ROOT / "main/protocols/websocket_protocol.cc")
        shared_guard = (
            "#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 || "
            "CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1"
        )
        features = protocol.split(shared_guard, 1)[1].split("#else", 1)[0]
        self.assertIn('"strict_playback_ack", true', features)
        self.assertIn('"device_stage_telemetry", true', features)
        self.assertIn('"pcm_mouth_sync", false', features)
        application = self.read_required(ROOT / "main/application.cc")
        for stage in ("speaker_pcm_started", "capture_started", "playback_drained"):
            before_stage = application.split(f'"{stage}"', 1)[0]
            self.assertEqual(before_stage.rsplit("#if", 1)[1].splitlines()[0],
                             shared_guard.removeprefix("#if"))

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

    def test_selfhosted_build_uses_offline_xiaocan_custom_wake_words(self):
        config = json.loads(self.read_required(BOARD / "config.json"))
        sdkconfig = "\n".join(config["builds"][0]["sdkconfig_append"])

        self.assertIn("CONFIG_USE_CUSTOM_WAKE_WORD=y", sdkconfig)
        self.assertIn('CONFIG_CUSTOM_WAKE_WORD="ni hao xiao can"', sdkconfig)
        self.assertIn('CONFIG_CUSTOM_WAKE_WORD_DISPLAY="你好小灿"', sdkconfig)
        self.assertIn('CONFIG_CUSTOM_WAKE_WORD_SECONDARY="xiao can"', sdkconfig)
        self.assertIn('CONFIG_CUSTOM_WAKE_WORD_SECONDARY_DISPLAY="小灿"', sdkconfig)
        self.assertIn("CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=15", sdkconfig)
        self.assertIn("CONFIG_SR_MN_CN_MULTINET5_RECOGNITION_QUANT8=y", sdkconfig)
        self.assertNotIn("CONFIG_USE_AFE_WAKE_WORD=y", sdkconfig)
        self.assertNotIn("CONFIG_SR_WN_WN9_NIHAOXIAOZHI_TTS=y", sdkconfig)

    def test_build_instructions_keep_compiled_custom_wake_configuration(self):
        readme = self.read_required(BOARD / "README.md")
        build_command = readme.split("```bash", 1)[1].split("```", 1)[0]

        self.assertNotIn("--wake-word", build_command)
        self.assertIn("你好小灿", readme)
        self.assertIn("小灿", readme)

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
        self.assertRegex(pins, r"#define\s+DISPLAY_WIDTH\s+320\b")
        self.assertRegex(pins, r"#define\s+DISPLAY_HEIGHT\s+240\b")
        self.assertRegex(pins, r"#define\s+DISPLAY_MIRROR_X\s+true\b")
        self.assertRegex(pins, r"#define\s+DISPLAY_MIRROR_Y\s+false\b")
        self.assertRegex(pins, r"#define\s+DISPLAY_SWAP_XY\s+true\b")

    def test_uses_simplex_audio_and_has_no_camera_runtime(self):
        source = self.read_required(BOARD / "hensun_nocam_pilot_v1_board.cc")

        self.assertIn("NoAudioCodecSimplex", source)
        self.assertIn("volume_up_button_", source)
        self.assertIn("volume_down_button_", source)
        self.assertIn("EnterWifiConfigMode();", source)
        self.assertNotIn("Camera", source)
        self.assertNotIn("LAMP_GPIO", source)

    def test_normalizes_left_aligned_i2s_mic_without_clipping(self):
        source = self.read_required(BOARD / "hensun_nocam_pilot_v1_board.cc")

        self.assertIn(
            "class HensunNoCamAudioCodecSimplex final : public NoAudioCodecSimplex",
            source,
        )
        self.assertIn("bit32_buffer[index] >> 16", source)
        self.assertIn("constexpr int kHensunNoCamMicInputGain = 4", source)
        self.assertIn(
            "(bit32_buffer[index] >> 16) * kHensunNoCamMicInputGain",
            source,
        )
        self.assertNotIn("bit32_buffer[index] >> 12", source)
        self.assertIn("Mic input level: avg_abs=", source)
        self.assertIn("static HensunNoCamAudioCodecSimplex audio_codec", source)

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

    def test_standard_emotes_are_animated_full_screen_landscape_gifs(self):
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
            self.assertEqual(struct.unpack("<HH", payload[6:10]), (320, 240))

    def test_maps_cloud_emotions_to_the_nine_standard_assets(self):
        source = self.read_required(BOARD / "hensun_nocam_pilot_v1_board.cc")

        self.assertIn("class HensunNoCamDisplay", source)
        expected_routes = {
            '"neutral", "neutral"',
            '"link", "neutral"',
            '"listening", "surprised"',
            '"speaking", "neutral"',
            '"relaxed", "neutral"',
            '"happy", "silly"',
            '"laughing", "silly"',
            '"caring", "caring"',
            '"affectionate", "caring"',
            '"curious", "confused"',
            '"thinking", "confused"',
            '"surprised", "surprised"',
            '"wake", "surprised"',
            '"confused", "confused"',
            '"concerned", "sad"',
            '"worried", "sad"',
            '"apology", "sad"',
            '"safe_block", "sad"',
            '"apologetic", "sad"',
            '"shy", "shy"',
            '"sad", "sad"',
        }
        for route in expected_routes:
            self.assertIn(route, source)
        self.assertIn('return "neutral";', source)

    def test_standby_uses_sleepy_face_after_reply_settle(self):
        source = self.read_required(BOARD / "hensun_nocam_pilot_v1_board.cc")
        application = self.read_required(ROOT / "main/application.cc")

        self.assertIn('{"idle", "sleepy"}', source)
        self.assertIn('{"sleepy", "sleepy"}', source)
        self.assertRegex(source, r'void BeginReplySettle\(\) override \{\s*SetEmotion\("caring"\);')
        self.assertRegex(application, r'if \(reply_pending_\) \{(?:(?!break;)[\s\S])*SetEmotion\("thinking"\);')
        self.assertRegex(application, r'case kDeviceStateListening:(?:(?!break;)[\s\S])*SetEmotion\("listening"\);')
        self.assertIn("void CompleteReplySettle() override", source)
        self.assertRegex(
            source,
            r"void CompleteReplySettle\(\) override \{\s*SetEmotion\(\"idle\"\);\s*\}",
        )
        self.assertRegex(
            application,
            r"CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1\s+"
            r"display->SetEmotion\(\"idle\"\);\s+#else\s+"
            r"display->SetEmotion\(\"neutral\"\);",
        )

    def test_standby_face_is_refreshed_even_when_state_is_already_idle(self):
        application = self.read_required(ROOT / "main/application.cc")
        abort_body = re.search(
            r"void Application::AbortDialogueToStandby\(.*?\) \{(.*?)\n\}",
            application,
            re.DOTALL,
        )

        self.assertIsNotNone(abort_body)
        self.assertRegex(
            abort_body.group(1),
            r"SetDeviceState\(kDeviceStateIdle\);\s*"
            r"#if CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1\s*"
            r"display->SetEmotion\(\"idle\"\);",
        )

    def test_completed_turn_emotion_cannot_overwrite_standby_face(self):
        application = self.read_required(ROOT / "main/application.cc")

        self.assertRegex(
            application,
            r"const bool stale_turn = !turn_id\.empty\(\) &&\s*"
            r"turn_id != active_turn_id_;",
        )
        self.assertRegex(
            application,
            r"const bool standby_without_reply =\s*"
            r"GetDeviceState\(\) == kDeviceStateIdle &&\s*"
            r"!reply_pending_ && !tts_playback_prepared_\.load\(\) &&\s*"
            r"active_tts_reply_id_\.empty\(\);",
        )
        self.assertIn("if (stale_turn || standby_without_reply)", application)

    def test_keeps_status_text_but_removes_ui_bar_backgrounds(self):
        source = self.read_required(BOARD / "hensun_nocam_pilot_v1_board.cc")

        self.assertIn("void SetupUI() override", source)
        self.assertIn("void SetTheme(Theme* theme) override", source)
        self.assertIn("SpiLcdDisplay::SetupUI();", source)
        self.assertIn("SpiLcdDisplay::SetTheme(theme);", source)
        self.assertIn(
            "lv_obj_set_style_bg_opa(top_bar_, LV_OPA_TRANSP, 0);", source
        )
        self.assertIn(
            "lv_obj_set_style_bg_opa(bottom_bar_, LV_OPA_TRANSP, 0);", source
        )
        self.assertIn(
            "lv_obj_set_style_text_color(status_label_, lv_color_white(), 0);",
            source,
        )
        self.assertNotIn(
            "lv_obj_add_flag(top_bar_, LV_OBJ_FLAG_HIDDEN)", source
        )
        self.assertNotIn(
            "lv_obj_add_flag(status_bar_, LV_OBJ_FLAG_HIDDEN)", source
        )

    def test_activation_screen_shows_qr_fragment_and_six_digit_fallback(self):
        source = self.read_required(BOARD / "hensun_nocam_pilot_v1_board.cc")
        config = self.read_required(BOARD / "config.json")
        ota_header = self.read_required(ROOT / "main/ota.h")
        ota_source = self.read_required(ROOT / "main/ota.cc")
        app_source = self.read_required(ROOT / "main/application.cc")

        self.assertIn('"CONFIG_LV_USE_QRCODE=y"', config)
        self.assertIn("void ShowActivationCode", source)
        self.assertIn("lv_qrcode_create", source)
        self.assertIn("/claim#code=", source)
        self.assertIn("GetActivationClaimUrl", ota_header)
        self.assertIn('cJSON_GetObjectItem(activation, "claim_url")', ota_source)
        self.assertIn("display->ShowActivationCode", app_source)
        self.assertIn('SetEmotion("surprised")', app_source)

    def test_code_only_claim_polls_without_challenge_activation_or_reannouncing(self):
        source = self.read_required(ROOT / "main/application.cc")
        check = source.split("void Application::CheckNewVersion()", 1)[1].split(
            "void Application::InitializeProtocol()", 1
        )[0]
        code_only = check.split("if (!ota_->HasActivationChallenge()) {", 1)[1].split(
            "// This will block", 1
        )[0]
        self.assertIn("vTaskDelay(pdMS_TO_TICKS(3000));", code_only)
        self.assertIn("continue;", code_only)
        self.assertNotIn("Activate()", code_only)
        self.assertIn("if (code != announced_activation_code)", check)
        self.assertIn("else if (!activation_prompt_visible)", check)
        # A transient error must restore the QR without queuing the same digit sounds.
        restore = check.split("else if (!activation_prompt_visible)", 1)[1].split("}", 1)[0]
        self.assertIn("display->ShowActivationCode", restore)
        self.assertNotIn("audio_service_", restore)
        self.assertIn("if (!activation_prompt_visible)", check.split("ota_->CheckVersion()", 1)[0])

    def test_nocam_preconnect_keeps_one_shot_excluded_and_defers_protocol_mutation(self):
        source = self.read_required(ROOT / "main/application.cc")
        guard = (
            "#if (CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 || "
            "CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1) && "
            "!CONFIG_HENSUN_ONE_SHOT_CONVERSATION"
        )
        self.assertEqual(source.count(guard), 3)
        self.assertEqual(source.count("protocol_->IsAudioChannelOpened()"), 1)
        self.assertEqual(source.count("protocol_->CloseAudioChannel()"), 1)
        self.assertEqual(source.count("protocol_.reset()"), 1)
        worker = source.split("void Application::EnsureControlChannelReady()", 1)[1].split(
            "bool Application::IsControlChannelReady()", 1
        )[0]
        self.assertLess(worker.index("control_channel_connecting_.store(true)"), worker.index("xTaskCreate("))
        completion = worker.split("app->Schedule([app, opened]()", 1)[1]
        self.assertIn("control_channel_connecting_.store(false)", completion)
        self.assertLess(completion.index("protocol_reset_pending_"), completion.index("auto apply ="))
        self.assertIn("ContinueOpenAudioChannel(app->listening_mode_)", completion)
        self.assertIn("Ota refresh;", worker)
        self.assertIn("!opened && app->protocol_is_websocket_", worker)
        self.assertNotIn("InitializeProtocol()", worker)

    def test_successful_reconnect_ignores_queued_old_error_and_close_notifications(self):
        source = self.read_required(ROOT / "main/application.cc")
        error = source.split("protocol_->OnNetworkError", 1)[1].split(
            "protocol_->OnIncomingAudio", 1
        )[0].split("Schedule(", 1)[1]
        closed = source.split("protocol_->OnAudioChannelClosed", 1)[1].split(
            "protocol_->OnIncomingJson", 1
        )[0].split("Schedule(", 1)[1]
        guard = "control_channel_connecting_.load() || IsControlChannelReady()"
        self.assertLess(error.index(guard), error.index("MAIN_EVENT_ERROR"))
        self.assertLess(closed.index(guard), closed.index("PowerSaveLevel::LOW_POWER"))
        self.assertLess(closed.index(guard), closed.index("AbortDialogueToStandby"))

    def test_lvgl_claim_qr_uses_only_one_qrcodegen_implementation(self):
        cmake = (
            ROOT
            / "managed_components"
            / "espressif2022__esp_emote_gfx"
            / "CMakeLists.txt"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1 AND CONFIG_LV_USE_QRCODE",
            cmake,
        )
        self.assertIn(
            'EXCLUDE REGEX ".*/lib/qrcode/qrcodegen\\\\.c$"',
            cmake,
        )

    def test_is_registered_as_an_esp32s3_board(self):
        kconfig = self.read_required(ROOT / "main/Kconfig.projbuild")
        cmake = self.read_required(ROOT / "main/CMakeLists.txt")

        self.assertIn("config BOARD_TYPE_HENSUN_NOCAM_PILOT_V1", kconfig)
        self.assertIn("depends on IDF_TARGET_ESP32S3", kconfig)
        self.assertIn("elseif(CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1)", cmake)
        self.assertIn('set(BOARD_DIR "hensun/hensun-nocam-pilot-v1")', cmake)


if __name__ == "__main__":
    unittest.main()
