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
        self.application_source = (ROOT / "main/application.cc").read_text(
            encoding="utf-8"
        )
        self.application_header = (ROOT / "main/application.h").read_text(
            encoding="utf-8"
        )
        self.audio_engine_source = (
            ROOT / "main/audio/engines/afe_audio_engine.cc"
        ).read_text(encoding="utf-8")
        self.face_header = (BOARD / "hensun_face_display.h").read_text(
            encoding="utf-8"
        )
        self.face_source = (BOARD / "hensun_face_display.cc").read_text(
            encoding="utf-8"
        )
        self.event_contract = (BOARD / "FACE_EVENT_CONTRACT.md").read_text(
            encoding="utf-8"
        )
        self.animation_spec = (BOARD / "FACE_ANIMATION_SPEC.md").read_text(
            encoding="utf-8"
        )
        self.animation_v2_spec = (BOARD / "FACE_ANIMATION_V2_SPEC.md").read_text(
            encoding="utf-8"
        )

    def test_has_isolated_official_selfhosted_and_emote_lab_variants(self):
        self.assertEqual(self.config["manufacturer"], "hensun")
        self.assertEqual(self.config["type"], "hensun-cam-pilot-v1")
        self.assertEqual(
            set(self.builds),
            {
                "hensun-cam-official-v1",
                "hensun-cam-selfhosted-v1",
                "hensun-cam-selfhosted-landscape-v1",
                "hensun-cam-emote-lab-v1",
            },
        )

        official = "\n".join(self.builds["hensun-cam-official-v1"]["sdkconfig_append"])
        selfhosted = "\n".join(
            self.builds["hensun-cam-selfhosted-v1"]["sdkconfig_append"]
        )
        landscape = "\n".join(
            self.builds["hensun-cam-selfhosted-landscape-v1"]["sdkconfig_append"]
        )
        for sdkconfig in (official, selfhosted, landscape):
            self.assertIn("CONFIG_USE_HOTSPOT_WIFI_PROVISIONING=y", sdkconfig)
            self.assertIn("CONFIG_USE_ESP_BLUFI_WIFI_PROVISIONING=n", sdkconfig)
            self.assertIn("CONFIG_SEND_WAKE_WORD_DATA=n", sdkconfig)
        self.assertIn("api.tenclass.net", official)
        self.assertNotIn("api.hensun", official)
        self.assertIn("api.hensun.invalid", selfhosted)
        self.assertNotIn("api.tenclass.net", selfhosted)
        self.assertIn("api.hensun.invalid", landscape)
        self.assertNotIn("api.tenclass.net", landscape)

    def test_audio_channel_refreshes_short_lived_token_before_connecting(self):
        method = re.search(
            r"bool Application::OpenAudioChannelWithConfigRefresh\(\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(method)
        body = method.group(1)
        self.assertIn("ota_ = std::make_unique<Ota>()", body)
        self.assertLess(body.index("ota_->CheckVersion()"), body.index("OpenAudioChannel()"))

    def test_selfhosted_tts_uses_ready_drained_handshake_and_queue_backpressure(self):
        protocol_header = (ROOT / "main/protocols/protocol.h").read_text(encoding="utf-8")
        protocol_source = (ROOT / "main/protocols/protocol.cc").read_text(encoding="utf-8")

        self.assertIn("SendTtsState", protocol_header)
        self.assertIn('"ready"', self.application_source)
        self.assertIn('"drained"', self.application_source)
        self.assertIn("reply_id", self.application_source)
        self.assertIn("pending_tts_stop_reply_id_", self.application_source)
        self.assertIn("PushPacketToDecodeQueue(std::move(packet), true)", self.application_source)
        self.assertIn("GetDecodeDropCount", self.application_source)
        self.assertIn('cJSON_AddStringToObject(root, "reply_id"', protocol_source)

    def test_auto_listening_waits_for_post_playback_echo_guard(self):
        self.assertRegex(
            self.application_source,
            r"kPostPlaybackListenGuardUs\s*=\s*1000\s*\*\s*1000",
        )
        self.assertIn("MAIN_EVENT_POST_PLAYBACK_GUARD", self.application_header)
        self.assertIn("post_playback_listen_timer_handle_", self.application_header)
        self.assertIn("post_playback_guard_active_", self.application_header)

        finish = re.search(
            r"void Application::FinishTtsPlayback\(std::string reply_id\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(finish)
        body = finish.group(1)
        self.assertIn("post_playback_guard_active_ = true", body)
        self.assertIn("esp_timer_start_once", body)
        self.assertLess(
            body.index("post_playback_guard_active_ = true"),
            body.index("SetDeviceState(kDeviceStateListening)"),
        )

        self.assertIn(
            "if (post_playback_guard_active_)", self.application_source
        )
        self.assertIn(
            "else if (!post_playback_guard_active_ && pending_listening_start_",
            self.application_source,
        )
        self.assertIn(
            "audio_service_.EnableVoiceProcessing(false)", self.application_source
        )
        run = re.search(
            r"void Application::Run\(\) \{(.*?)\n\}\n\nvoid Application::HandleNetworkConnectedEvent",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(run)
        self.assertIn("bits & MAIN_EVENT_POST_PLAYBACK_GUARD", run.group(1))

    def test_auto_listening_has_a_bounded_safety_timeout(self):
        self.assertRegex(
            self.application_source,
            r"kAutoStopListeningTimeoutTicks\s*=\s*15",
        )
        self.assertIn(
            "clock_ticks_ >= kAutoStopListeningTimeoutTicks",
            self.application_source,
        )

    def test_hensun_cam_uses_noise_tolerant_vad_settings(self):
        self.assertIn(
            "#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1",
            self.audio_engine_source,
        )
        self.assertIn("afe_config->vad_mode = VAD_MODE_2", self.audio_engine_source)
        self.assertIn(
            "afe_config->vad_min_noise_ms = 600",
            self.audio_engine_source,
        )

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

    def test_has_original_60_scene_face_showcase_without_bitmap_assets(self):
        states = [
            "kListeningStarted",
            "kPositiveResponse",
            "kClarificationNeeded",
            "kProcessingStarted",
            "kConfirmationRequired",
            "kAsrLowConfidence",
            "kNoisyEnvironment",
            "kUserContinueExpected",
            "kUserInterruptedAssistant",
            "kBootReady",
            "kWakeWordDetected",
            "kIdleEntered",
            "kPairingModeEntered",
            "kNetworkConnected",
            "kChargingStarted",
            "kChargeComplete",
            "kSleepEntered",
            "kMildAmusement",
            "kStrongAmusement",
            "kPositiveSurprise",
            "kComplimentReceived",
            "kAchievementCelebration",
            "kEncouragementRequested",
            "kThanksReceived",
            "kAffectionReceived",
            "kCuriosityEngaged",
            "kSadnessDetected",
            "kComfortModeEntered",
            "kWorryDetected",
            "kAngerDetected",
            "kFearDetected",
            "kLonelinessDetected",
            "kFatigueDetected",
            "kUnfairnessDistress",
            "kDisappointmentDetected",
            "kAssistantApologyRequired",
            "kFirstInteraction",
            "kMorningGreeting",
            "kNoonGreeting",
            "kBedtimeGreeting",
            "kReturnAfterAbsence",
            "kBirthdayGreeting",
            "kHolidayGreeting",
            "kMealCheckIn",
            "kReminderCreated",
            "kReminderDue",
            "kTimerStarted",
            "kTimerFinished",
            "kAlarmTriggered",
            "kVolumeChanged",
            "kModeChanged",
            "kQueryResultReady",
            "kNetworkUnavailable",
            "kCloudServiceUnavailable",
            "kBatteryLow",
            "kBatteryCritical",
            "kDeviceOverheat",
            "kMicrophoneFault",
            "kContentSafetyBlocked",
            "kUserCrisisDetected",
        ]
        for state in states:
            self.assertIn(state, self.face_header)
            self.assertRegex(
                self.face_source,
                rf"case HensunFaceState::{state}:",
                state,
            )
        enum_states = re.findall(
            r"^\s+(k[A-Za-z0-9]+)(?:\s*=\s*0)?,?$", self.face_header, re.MULTILINE
        )
        self.assertEqual(len([state for state in enum_states if state != "kCount"]), 60)
        self.assertIn("HensunFaceDisplay", self.source)
        self.assertIn("StartShowcase", self.source)
        self.assertIn("OnLongPress", self.source)
        self.assertIn("OnDoubleClick", self.source)
        self.assertIn("EnterWifiConfigMode", self.source)
        self.assertIn("kAnimationPeriodMs = 50", self.face_source)
        self.assertIn("kShowcaseSceneCount = 60", self.face_source)
        self.assertIn("Company-derived 60-scene face", self.face_source)
        self.assertIn("Starting 60-scene display showcase", self.face_source)
        self.assertIn("60-scene display showcase complete", self.face_source)
        self.assertIn("LVGL face update avg=", self.face_source)
        self.assertIn("lv_obj_create", self.face_source)
        self.assertIn("lv_arc_create", self.face_source)
        self.assertNotRegex(self.face_source, r"\.(?:png|gif|jpg|jpeg)\b")

    def test_company_expression_package_drives_device_native_symbols(self):
        catalog_path = BOARD / "hensun_face_catalog.json"
        assets_header_path = BOARD / "hensun_face_assets.h"
        assets_source_path = BOARD / "hensun_face_assets.c"
        generator_path = BOARD / "tools/generate_face_assets.py"
        provenance_path = BOARD / "ASSET_PROVENANCE.md"

        for path in (
            catalog_path,
            assets_header_path,
            assets_source_path,
            generator_path,
            provenance_path,
        ):
            self.assertTrue(path.is_file(), path)

        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        scenes = catalog["scenes"]
        symbols = sorted({scene["symbol"] for scene in scenes if scene["symbol"] != "none"})
        self.assertEqual(catalog["source"]["style_version"], "HENSUN_FACE_V1.0")
        self.assertEqual(
            catalog["source"]["manifest_sha256"],
            "ef297b34bb0d7e33915392513b2142d5a9b47bac912bd87e2f2b4151c79693a6",
        )
        self.assertEqual(len(scenes), 60)
        self.assertEqual(len(symbols), 36)
        self.assertEqual([scene["id"] for scene in scenes], [f"{i:03d}" for i in range(1, 61)])

        assets_header = assets_header_path.read_text(encoding="utf-8")
        assets_source = assets_source_path.read_text(encoding="utf-8")
        provenance = provenance_path.read_text(encoding="utf-8")
        self.assertIn("#define HENSUN_FACE_SCENE_COUNT 60", assets_header)
        self.assertIn("#define HENSUN_FACE_SYMBOL_COUNT 36", assets_header)
        self.assertIn("LV_COLOR_FORMAT_A4", assets_source)
        self.assertIn("HensunFaceSceneImage", assets_header)
        self.assertIn("HensunFaceSceneAccentRgb", assets_header)
        for symbol in symbols:
            c_name = f"hensun_symbol_{symbol}"
            self.assertIn(f"LV_IMAGE_DECLARE({c_name})", assets_header)
            self.assertIn(f"const lv_image_dsc_t {c_name}", assets_source)

        self.assertIn("lv_image_create", self.face_source)
        self.assertIn("HensunFaceSceneImage(state_)", self.face_source)
        self.assertIn("HensunFaceSceneAccentRgb(state_)", self.face_source)
        self.assertNotIn("accent_label_", self.face_header)
        self.assertNotIn("lv_label_create(face_layer_)", self.face_source)
        self.assertNotIn("lv_label_set_text(accent_label_", self.face_source)

        self.assertIn("--check", generator_path.read_text(encoding="utf-8"))
        self.assertIn("product owner confirmed", provenance)

    def test_face_has_bounded_state_specific_motion_profiles(self):
        self.assertIn("kEntryAnimationFrames = 8", self.face_source)
        self.assertIn("struct FaceMotion", self.face_source)
        self.assertIn("MotionFamily MotionFamilyForState", self.face_source)
        self.assertIn("FaceMotion MotionForState", self.face_source)

        motion_routes = self.face_source.split(
            "MotionFamily MotionFamilyForState", 1
        )[1].split("FaceMotion MotionForState", 1)[0]
        expected_routes = {
            "kListeningStarted": "kListen",
            "kWakeWordDetected": "kListen",
            "kProcessingStarted": "kThink",
            "kClarificationNeeded": "kThink",
            "kQueryResultReady": "kSpeak",
            "kPositiveResponse": "kCelebrate",
            "kAchievementCelebration": "kCelebrate",
            "kSleepEntered": "kSleep",
            "kAlarmTriggered": "kAlert",
            "kPairingModeEntered": "kStatus",
            "kChargingStarted": "kStatus",
            "kContentSafetyBlocked": "kRestrained",
            "kUserCrisisDetected": "kRestrained",
        }
        for state, family in expected_routes.items():
            self.assertRegex(
                motion_routes,
                rf"HensunFaceState::{state}:.*?MotionFamily::{family}",
            )

        motion_states = re.findall(r"case HensunFaceState::(k[A-Za-z0-9]+):", motion_routes)
        enum_states = re.findall(
            r"^\s+(k[A-Za-z0-9]+)(?:\s*=\s*0)?,?$", self.face_header, re.MULTILINE
        )
        self.assertEqual(len(motion_states), len(set(motion_states)))
        self.assertEqual(set(motion_states), set(enum_states))

        for api in (
            "lv_image_set_scale",
            "lv_image_set_rotation",
            "motion.face_x",
            "motion.face_y",
            "motion.eye_shift_x",
            "motion.mouth_y",
            "motion.cheek_y",
        ):
            self.assertIn(api, self.face_source)

        self.assertIn("existing 50 ms timer period (20 FPS)", self.animation_spec)
        self.assertIn("at most 6 pixels", self.animation_spec)
        self.assertIn("no full-screen or high-frequency", self.animation_spec)
        self.assertIn("average LVGL face update below 8 ms", self.animation_spec)

    def test_face_uses_real_speaker_pcm_for_lip_sync(self):
        self.assertIn("class HensunAudioCodecSimplex", self.source)
        self.assertRegex(
            self.source,
            r"void\s+OutputData\(std::vector<int16_t>&\s+data\)\s+override",
        )
        self.assertIn("kSpeechPcmSampleStride = 8", self.source)
        self.assertIn("display_->SetSpeechLevel", self.source)
        self.assertIn("AudioCodec::OutputData(data)", self.source)

        self.assertIn("void SetSpeechLevel(uint8_t level)", self.face_header)
        self.assertIn("std::atomic<uint8_t> speech_level_", self.face_header)
        self.assertIn("std::atomic<uint32_t> speech_level_updated_ms_", self.face_header)
        self.assertIn("UpdateSpeechEnvelope", self.face_source)
        self.assertIn("kSpeechLevelStaleMs", self.face_source)
        self.assertIn("kDeviceStateSpeaking", self.face_source)
        self.assertIn("speech_level_smoothed_", self.face_source)
        self.assertIn("Audio-reactive mouth", self.face_source)

        self.assertIn("decoded PCM -> Hensun board codec sampler", self.animation_v2_spec)
        self.assertIn("Silence closes", self.animation_v2_spec)
        self.assertIn("never calls LVGL", self.animation_v2_spec)
        self.assertIn("no heap allocation", self.animation_v2_spec)

    def test_face_has_natural_blink_gaze_and_eased_entry(self):
        for marker in (
            "kBlinkClosedFrames = 3",
            "NextPseudoRandom",
            "UpdateAmbientMotion",
            "blink_frames_remaining_",
            "next_blink_frame_",
            "gaze_target_x_",
            "gaze_x_",
            "EaseOutEntry",
        ):
            self.assertIn(marker, self.face_source + self.face_header)

        self.assertNotIn(
            "state_ == HensunFaceState::kIdleEntered && animation_frame_ % 100 >= 94",
            self.face_source,
        )
        self.assertIn("three-frame blinks", self.animation_v2_spec)
        self.assertIn("Ambient gaze is limited to two pixels", self.animation_v2_spec)
        self.assertIn("integer ease-out entry motion", self.animation_v2_spec)

    def test_face_states_are_bound_to_real_event_inputs(self):
        canonical_inputs = [
            "listening_started", "positive_response", "clarification_needed",
            "processing_started", "confirmation_required", "asr_low_confidence",
            "noisy_environment", "user_continue_expected",
            "user_interrupted_assistant", "boot_ready", "wake_word_detected",
            "idle_entered", "pairing_mode_entered", "network_connected",
            "charging_started", "charge_complete", "sleep_entered",
            "mild_amusement", "strong_amusement", "positive_surprise",
            "compliment_received", "achievement_celebration",
            "encouragement_requested", "thanks_received", "affection_received",
            "curiosity_engaged", "sadness_detected", "comfort_mode_entered",
            "worry_detected", "anger_detected", "fear_detected",
            "loneliness_detected", "fatigue_detected", "unfairness_distress",
            "disappointment_detected", "assistant_apology_required",
            "first_interaction", "morning_greeting", "noon_greeting",
            "bedtime_greeting", "return_after_absence", "birthday_greeting",
            "holiday_greeting", "meal_check_in", "reminder_created",
            "reminder_due", "timer_started", "timer_finished", "alarm_triggered",
            "volume_changed", "mode_changed", "query_result_ready",
            "network_unavailable", "cloud_service_unavailable", "battery_low",
            "battery_critical", "device_overheat", "microphone_fault",
            "content_safety_blocked", "user_crisis_detected",
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

    def test_supports_xiaozhi_standard_21_emotions(self):
        standard_routes = {
            "happy": "kPositiveResponse",
            "laughing": "kStrongAmusement",
            "funny": "kMildAmusement",
            "sad": "kSadnessDetected",
            "angry": "kAngerDetected",
            "crying": "kUnfairnessDistress",
            "loving": "kAffectionReceived",
            "embarrassed": "kComplimentReceived",
            "surprised": "kPositiveSurprise",
            "shocked": "kPositiveSurprise",
            "thinking": "kProcessingStarted",
            "winking": "kFirstInteraction",
            "cool": "kEncouragementRequested",
            "relaxed": "kComfortModeEntered",
            "delicious": "kMealCheckIn",
            "kissy": "kAffectionReceived",
            "confident": "kEncouragementRequested",
            "sleepy": "kFatigueDetected",
            "silly": "kMildAmusement",
            "confused": "kClarificationNeeded",
        }
        for emotion, state in standard_routes.items():
            self.assertRegex(
                self.face_source,
                rf'\{{"{emotion}",\s*HensunFaceState::{state}',
            )
            self.assertIn(f"`{emotion}`", self.event_contract)

        self.assertIn('std::strcmp(emotion, "neutral") == 0', self.face_source)
        self.assertIn("`neutral`", self.event_contract)

    def test_face_layer_preserves_camera_preview_and_hides_battery_ui(self):
        self.assertIn("LcdDisplay::SetPreviewImage", self.face_source)
        self.assertIn("preview_active_", self.face_source)
        self.assertIn("lv_obj_add_flag(face_layer_", self.face_source)
        self.assertIn("lv_obj_add_flag(battery_label_", self.face_source)


if __name__ == "__main__":
    unittest.main()
