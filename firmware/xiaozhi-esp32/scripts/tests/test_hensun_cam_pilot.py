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
                "hensun-cam-selfhosted-landscape-local-v1",
                "hensun-cam-emote-lab-v1",
            },
        )

        official = "\n".join(self.builds["hensun-cam-official-v1"]["sdkconfig_append"])
        selfhosted = "\n".join(
            self.builds["hensun-cam-selfhosted-v1"]["sdkconfig_append"]
        )
        for sdkconfig in (official, selfhosted):
            self.assertIn("CONFIG_USE_HOTSPOT_WIFI_PROVISIONING=y", sdkconfig)
            self.assertIn("CONFIG_USE_ESP_BLUFI_WIFI_PROVISIONING=n", sdkconfig)
            self.assertIn("CONFIG_SEND_WAKE_WORD_DATA=n", sdkconfig)
        self.assertIn("api.tenclass.net", official)
        self.assertNotIn("api.hensun", official)
        self.assertIn("api.hensun.invalid", selfhosted)
        self.assertNotIn("api.tenclass.net", selfhosted)

    def test_audio_channel_reuses_a_valid_token_before_refreshing_bootstrap(self):
        method = re.search(
            r"bool Application::OpenAudioChannelWithConfigRefresh\(\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(method)
        body = method.group(1)
        self.assertIn("if (protocol_ && protocol_->OpenAudioChannel())", body)
        self.assertIn("ota_ = std::make_unique<Ota>()", body)
        self.assertLess(
            body.index("if (protocol_ && protocol_->OpenAudioChannel())"),
            body.index("ota_->CheckVersion()"),
        )
        self.assertIn("Bootstrap is only needed", body)

    def test_first_utterance_is_buffered_while_the_audio_channel_connects(self):
        for method_name, signature in (
            ("manual", r"void Application::ContinueOpenAudioChannel\(ListeningMode mode\) \{(.*?)\n\}"),
            ("wake", r"void Application::ContinueWakeWordInvoke\(const std::string& wake_word\) \{(.*?)\n\}"),
        ):
            method = re.search(signature, self.application_source, re.DOTALL)
            self.assertIsNotNone(method, method_name)
            body = method.group(1)
            self.assertIn("audio_service_.EnableVoiceProcessing(true)", body)
            self.assertLess(
                body.index("audio_service_.EnableVoiceProcessing(true)"),
                body.index("OpenAudioChannelWithConfigRefresh()"),
            )

        listening = re.search(
            r"void Application::StartListeningAudio\(\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(listening)
        body = listening.group(1)
        self.assertIn("if (!audio_service_.IsAudioProcessorRunning())", body)
        self.assertLess(
            body.index("protocol_->SendStartListening(listening_mode_);"),
            body.index("xEventGroupSetBits(event_group_, MAIN_EVENT_SEND_AUDIO);"),
        )

    def test_device_serial_logs_do_not_emit_conversation_transcripts(self):
        self.assertNotIn('ESP_LOGI(TAG, "<< %s", text->valuestring);', self.application_source)
        self.assertNotIn('ESP_LOGI(TAG, ">> %s", text->valuestring);', self.application_source)
        self.assertIn("assistant text received (%u bytes)", self.application_source)
        self.assertIn("user text received (%u bytes)", self.application_source)

    def test_wake_word_starts_capture_before_the_connecting_transition(self):
        """Keep the question following a wake word in the local audio buffer."""
        method = re.search(
            r"void Application::BeginWakeWordInvoke\(const std::string& wake_word\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(method)
        body = method.group(1)
        self.assertIn("if (!protocol_->IsAudioChannelOpened())", body)
        self.assertIn("audio_service_.EnableVoiceProcessing(true)", body)
        self.assertLess(
            body.index("audio_service_.EnableVoiceProcessing(true)"),
            body.index("SetDeviceState(kDeviceStateConnecting)"),
        )

        continue_method = re.search(
            r"void Application::ContinueWakeWordInvoke\(const std::string& wake_word\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(continue_method)
        self.assertIn(
            "if (!audio_service_.IsAudioProcessorRunning())",
            continue_method.group(1),
        )

    def test_custom_wake_resets_multinet_between_idle_turns(self):
        source = (
            ROOT / "main/audio/wake_words/custom_wake_word.cc"
        ).read_text(encoding="utf-8")
        for method_name in ("Start", "Stop"):
            method = re.search(
                rf"void CustomWakeWord::{method_name}\(\) \{{(.*?)\n\}}",
                source,
                re.DOTALL,
            )
            self.assertIsNotNone(method)
            self.assertIn("input_buffer_.clear();", method.group(1))
            self.assertIn("multinet_->clean(multinet_model_data_);", method.group(1))

    def test_custom_wake_aligns_multinet_window_to_vad_speech_onset(self):
        custom_source = (
            ROOT / "main/audio/wake_words/custom_wake_word.cc"
        ).read_text(encoding="utf-8")
        custom_header = (
            ROOT / "main/audio/wake_words/custom_wake_word.h"
        ).read_text(encoding="utf-8")
        handler = re.search(
            r"void AfeAudioEngine::HandleWakeWordResult\(const afe_fetch_result_t\* result\) \{(.*?)\n\}",
            self.audio_engine_source,
            re.DOTALL,
        )
        self.assertIsNotNone(handler)
        body = handler.group(1)
        self.assertIn("result->vad_state == VAD_SPEECH", body)
        self.assertIn("custom_wake_word_->BeginSpeechWindow()", body)
        self.assertLess(
            body.index("custom_wake_word_->BeginSpeechWindow()"),
            body.index("custom_wake_word_->FeedMono("),
        )
        self.assertIn("void BeginSpeechWindow();", custom_header)
        self.assertIn("void CustomWakeWord::BeginSpeechWindow()", custom_source)

    def test_custom_wake_allocates_command_table_before_update(self):
        source = (
            ROOT / "main/audio/wake_words/custom_wake_word.cc"
        ).read_text(encoding="utf-8")
        initialize = re.search(
            r"bool CustomWakeWord::Initialize\(AudioCodec\* codec, srmodel_list_t\* models_list\) \{(.*?)\n\}",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(initialize)
        body = initialize.group(1)
        self.assertLess(
            body.index("esp_mn_commands_alloc(multinet_, multinet_model_data_)"),
            body.index("esp_mn_commands_add(i + 1, commands_[i].command.c_str())"),
        )
        self.assertLess(
            body.index("esp_mn_commands_add(i + 1, commands_[i].command.c_str())"),
            body.index("esp_mn_commands_update()"),
        )

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

    def test_selfhosted_websocket_uses_real_device_heartbeat(self):
        protocol_header = (ROOT / "main/protocols/protocol.h").read_text(
            encoding="utf-8"
        )
        protocol_source = (ROOT / "main/protocols/protocol.cc").read_text(
            encoding="utf-8"
        )
        websocket_source = (
            ROOT / "main/protocols/websocket_protocol.cc"
        ).read_text(encoding="utf-8")

        self.assertIn("bool SupportsHeartbeat() const", protocol_header)
        self.assertIn("bool SendHeartbeat(uint32_t sequence)", protocol_header)
        self.assertIn('cJSON_AddStringToObject(root, "type", "ping")', protocol_source)
        self.assertIn('strcmp(type->valuestring, "pong") == 0', self.application_source)
        self.assertIn('cJSON_GetObjectItem(features, "heartbeat")', websocket_source)
        self.assertIn(
            'cJSON_AddBoolToObject(features, "strict_playback_ack", true)',
            websocket_source,
        )
        self.assertRegex(
            self.application_source,
            r"kHeartbeatIntervalTicks\s*=\s*15",
        )
        self.assertRegex(
            self.application_source,
            r"kHeartbeatMissLimit\s*=\s*3",
        )
        self.assertIn("protocol_->SendHeartbeat", self.application_source)
        self.assertIn('RecoverFailedTurnToStandby("heartbeat-timeout", true)', self.application_source)

    def test_asr_noise_resume_reopens_listening_without_fake_tts(self):
        self.assertIn('strcmp(type->valuestring, "listen") == 0', self.application_source)
        self.assertIn('strcmp(state->valuestring, "resume") == 0', self.application_source)
        self.assertIn('strcmp(state->valuestring, "standby") == 0', self.application_source)
        self.assertIn('cJSON_GetObjectItem(root, "reason")', self.application_source)
        self.assertIn('reason_str == "user-exit"', self.application_source)
        self.assertIn("Ignoring listen resume while playback is active", self.application_source)
        self.assertIn("SetListeningMode(GetDefaultListeningMode())", self.application_source)

    def test_hensun_speaking_face_waits_for_real_pcm_and_keeps_a_reply_window(self):
        display_source = (BOARD / "hensun_emote_lab_display.cc").read_text(
            encoding="utf-8"
        )
        display_header = (ROOT / "main/display/display.h").read_text(encoding="utf-8")

        self.assertIn("virtual void BeginReplySettle() {}", display_header)
        self.assertIn("virtual void CompleteReplySettle() {}", display_header)
        self.assertIn("kAwaitingAudio", display_source)
        self.assertIn("if (awaiting_audio_.exchange(false))", display_source)
        self.assertIn(
            "QueueAnimation(ConversationAnimation(), false, true);", display_source
        )
        awaiting_audio = re.search(
            r"else if \(std::strcmp\(status, Lang::Strings::SPEAKING\) == 0\) \{"
            r"(.*?)else if \(std::strcmp\(status, Lang::Strings::ERROR\) == 0\)",
            display_source,
            re.DOTALL,
        )
        self.assertIsNotNone(awaiting_audio)
        self.assertNotIn('QueueAnimation("thinking"', awaiting_audio.group(1))
        self.assertIn("BeginReplySettle", self.application_source)

        finish = re.search(
            r"void Application::FinishTtsPlayback\(std::string reply_id\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(finish)
        self.assertLess(
            finish.group(1).index("BeginReplySettle("),
            finish.group(1).index("SetDeviceState(kDeviceStateIdle)"),
        )
        self.assertIn("kReplySettleDurationUs = 800 * 1000", self.application_source)
        self.assertNotIn("esp_timer_start_once", display_source)
        self.assertNotIn("IdleSleepTimerCallback", display_source)
        self.assertNotIn("EnterSleepAfterIdle", display_source)
        self.assertNotIn("reply_settle_pending_", display_source)
        self.assertIn(
            "presentation_state_.load() != PresentationState::kReplySettle",
            display_source,
        )

    def test_hensun_reports_privacy_safe_device_stage_events(self):
        protocol_header = (ROOT / "main/protocols/protocol.h").read_text(encoding="utf-8")
        protocol_source = (ROOT / "main/protocols/protocol.cc").read_text(encoding="utf-8")

        self.assertIn("SendDeviceStage", protocol_header)
        self.assertIn('"type", "device_stage"', protocol_source)
        self.assertIn('SendDeviceStage("capture_started"', self.application_source)
        self.assertIn('"speaker_pcm_started"', self.application_source)
        self.assertIn('SendDeviceStage("playback_drained"', self.application_source)
        self.assertNotIn('cJSON_AddStringToObject(root, "text"', protocol_source)

    def test_post_speech_reply_wait_does_not_flash_the_sleep_face(self):
        self.assertIn("reply_pending_", self.application_header)

        vad_stop = re.search(
            r"else if \(vad_speech_detected_\) \{(.*?)StopListening\(\);",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(vad_stop)
        self.assertIn("reply_pending_ = true", vad_stop.group(1))

        idle_state = re.search(
            r"case kDeviceStateUnknown:\s*case kDeviceStateIdle:(.*?)"
            r"case kDeviceStateConnecting:",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(idle_state)
        self.assertIn("if (reply_pending_)", idle_state.group(1))
        pending_branch = re.search(
            r"if \(reply_pending_\) \{(.*?)\}\s*display->SetStatus",
            idle_state.group(1),
            re.DOTALL,
        )
        self.assertIsNotNone(pending_branch)
        self.assertNotIn("SetStatus", pending_branch.group(1))
        self.assertIn(
            "audio_service_.EnableVoiceProcessing(false)", pending_branch.group(1)
        )
        self.assertIn(
            "audio_service_.EnableWakeWordDetection(false)", pending_branch.group(1)
        )

        playback_started = re.search(
            r"if \(bits & MAIN_EVENT_PLAYBACK_STARTED\) \{(.*?)\n        \}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(playback_started)
        self.assertLess(
            playback_started.group(1).index("reply_pending_ = false"),
            playback_started.group(1).index("SetDeviceState(kDeviceStateSpeaking)"),
        )

    def test_selfhosted_emote_profile_uses_a_matching_landscape_canvas(self):
        config = (BOARD / "config.h").read_text(encoding="utf-8")
        profile = json.loads((BOARD / "display_profiles.json").read_text(encoding="utf-8"))
        generated = (BOARD / "display_profile_generated.h").read_text(encoding="utf-8")
        spec = json.loads(
            (BOARD / "emote_lab/source/hensun_emote_motion_spec.json").read_text(
                encoding="utf-8"
            )
        )
        packer = (BOARD / "tools/pack_emote_lab_assets.mjs").read_text(encoding="utf-8")

        self.assertIn('#include "display_profile_generated.h"', config)
        self.assertEqual(profile["schema_version"], 1)
        self.assertEqual(
            profile["profiles"]["selfhosted-landscape"]["logical_size"],
            {"width": 320, "height": 240},
        )
        self.assertIn("#if CONFIG_USE_EMOTE_MESSAGE_STYLE", generated)
        self.assertIn("#define DISPLAY_WIDTH 320", generated)
        self.assertIn("#define DISPLAY_HEIGHT 240", generated)
        self.assertIn("#define DISPLAY_SWAP_XY true", generated)
        self.assertEqual(spec["canvas"], {"width": 320, "height": 240, "fps": 20})
        self.assertIn("wasm.wasmconvertoptions_set_resize(options, 320, 240)", packer)

    def test_auto_listening_waits_for_post_playback_echo_guard(self):
        self.assertRegex(
            self.application_source,
            r"kReplySettleDurationUs\s*=\s*800\s*\*\s*1000",
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
        self.assertIn("BeginReplySettle(true)", body)
        settle = re.search(
            r"void Application::BeginReplySettle\(bool resume_listening\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(settle)
        self.assertIn("post_playback_guard_active_ = resume_listening", settle.group(1))
        self.assertIn("esp_timer_start_once", settle.group(1))
        self.assertLess(
            body.index("BeginReplySettle(true)"),
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
        self.assertIn("CompleteReplySettle()", run.group(1))

    def test_auto_listening_has_a_bounded_safety_timeout(self):
        self.assertRegex(
            self.application_source,
            r"kWaitForSpeechTimeoutTicks\s*=\s*10",
        )
        self.assertRegex(
            self.application_source,
            r"kMaximumSpeechDurationTicks\s*=\s*20",
        )
        self.assertIn(
            "clock_ticks_ >= kWaitForSpeechTimeoutTicks",
            self.application_source,
        )
        self.assertIn("AbortSpeaking(kAbortReasonNone)", self.application_source)

    def test_follow_up_timeout_starts_with_capture_and_preserves_vad_edge(self):
        self.assertIn("listening_capture_ready_", self.application_header)
        self.assertIn("vad_speech_edge_pending_", self.application_header)

        listening = re.search(
            r"void Application::StartListeningAudio\(\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(listening)
        self.assertIn("clock_ticks_ = 0", listening.group(1))
        self.assertIn("listening_capture_ready_ = true", listening.group(1))

        self.assertRegex(
            self.application_source,
            r"kDeviceStateListening\s*&&\s*listening_capture_ready_\s*&&",
        )
        self.assertIn(
            "!vad_speech_edge_pending_.load(std::memory_order_acquire)",
            self.application_source,
        )
        self.assertLess(
            self.application_source.index(
                "vad_speech_edge_pending_.store(true, std::memory_order_release)"
            ),
            self.application_source.index("Schedule([this, speaking]()"),
        )

    def test_failed_cloud_turn_recovers_standby_and_wake_detection(self):
        self.assertIn("RecoverFailedTurnToStandby", self.application_header)
        self.assertRegex(
            self.application_source,
            r"kReplyPendingTimeoutTicks\s*=\s*12",
        )
        self.assertIn(
            "reply_pending_ && clock_ticks_ >= kReplyPendingTimeoutTicks",
            self.application_source,
        )

        recovery = re.search(
            r"void Application::RecoverFailedTurnToStandby\("
            r"const char\* reason, bool close_audio_channel\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(recovery)
        body = recovery.group(1)
        self.assertIn("reply_pending_ = false", body)
        self.assertIn("audio_service_.ResetDecoder()", body)
        self.assertIn("SetDeviceState(kDeviceStateIdle)", body)
        self.assertIn("display->SetStatus(Lang::Strings::STANDBY)", body)
        self.assertIn("audio_service_.EnableVoiceProcessing(false)", body)
        self.assertIn("audio_service_.EnableWakeWordDetection(true)", body)

        # A socket closure after real PCM must keep the bounded reply settle
        # instead of jumping straight from the speaking face to sleep.
        self.assertIn("const bool had_audio = tts_audio_started_", body)
        self.assertIn("if (had_audio)", body)
        self.assertIn("BeginReplySettle(false)", body)
        self.assertLess(
            body.index("BeginReplySettle(false)"),
            body.index("SetDeviceState(kDeviceStateIdle)"),
        )

        self.assertIsNotNone(
            re.search(
                r'else if \(strcmp\(type->valuestring, "error"\) == 0\).*?'
                r'RecoverFailedTurnToStandby',
                self.application_source,
                re.DOTALL,
            )
        )
        self.assertIn(
            'RecoverFailedTurnToStandby("audio-channel-closed", false)',
            self.application_source,
        )

    def test_selfhosted_emote_state_is_not_overwritten_by_neutral(self):
        display_source = (BOARD / "hensun_emote_lab_display.cc").read_text(
            encoding="utf-8"
        )
        self.assertIn('QueueAnimation("sleep")', display_source)
        self.assertIn('QueueAnimation("thinking", false, true)', display_source)
        self.assertNotIn(
            'display->SetStatus(Lang::Strings::LISTENING);\n            display->SetEmotion("neutral");',
            self.application_source,
        )

    def test_selfhosted_custom_wake_returns_to_idle_after_each_reply(self):
        selfhosted = "\n".join(
            self.builds["hensun-cam-selfhosted-v1"]["sdkconfig_append"]
        )
        official = "\n".join(
            self.builds["hensun-cam-official-v1"]["sdkconfig_append"]
        )
        self.assertIn("CONFIG_HENSUN_ONE_SHOT_CONVERSATION=y", selfhosted)
        self.assertNotIn("CONFIG_HENSUN_ONE_SHOT_CONVERSATION=y", official)

        finish = re.search(
            r"void Application::FinishTtsPlayback\(std::string reply_id\) \{(.*?)\n\}",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(finish)
        body = finish.group(1)
        self.assertIn("CONFIG_HENSUN_ONE_SHOT_CONVERSATION", body)
        self.assertIn("SetDeviceState(kDeviceStateIdle)", body)
        one_shot = re.search(
            r"#if CONFIG_HENSUN_ONE_SHOT_CONVERSATION(.*?)#else",
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(one_shot)
        self.assertIn("protocol_->CloseAudioChannel()", one_shot.group(1))

    def test_hensun_half_duplex_disables_wake_word_detection_while_speaking(self):
        speaking = re.search(
            r"case kDeviceStateSpeaking:(.*?)case kDeviceStateWifiConfiguring:",
            self.application_source,
            re.DOTALL,
        )
        self.assertIsNotNone(speaking)
        hensun = re.search(
            r"#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1(.*?)#else",
            speaking.group(1),
            re.DOTALL,
        )
        self.assertIsNotNone(hensun)
        self.assertIn(
            "audio_service_.EnableWakeWordDetection(false)",
            hensun.group(1),
        )

    def test_local_landscape_variant_keeps_a_ten_second_followup_window(self):
        local = "\n".join(
            self.builds["hensun-cam-selfhosted-landscape-local-v1"]["sdkconfig_append"]
        )
        self.assertIn("CONFIG_HENSUN_ONE_SHOT_CONVERSATION=n", local)
        self.assertNotIn("CONFIG_HENSUN_ONE_SHOT_CONVERSATION=y", local)
        self.assertNotIn("CONFIG_HENSUN_DIAGNOSTIC_AUTO_LISTEN_ON_BOOT=y", local)
        self.assertIn("CONFIG_USE_EMOTE_MESSAGE_STYLE=y", local)
        self.assertIn("CONFIG_USE_CUSTOM_WAKE_WORD=y", local)
        self.assertIn('CONFIG_CUSTOM_WAKE_WORD="ni hao xiao can"', local)
        self.assertIn('CONFIG_CUSTOM_WAKE_WORD_DISPLAY="你好小灿"', local)
        self.assertIn("CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=12", local)
        self.assertIn("CONFIG_SR_MN_CN_MULTINET5_RECOGNITION_QUANT8=y", local)
        self.assertNotIn("CONFIG_USE_AFE_WAKE_WORD=y", local)
        self.assertNotIn("CONFIG_SR_WN_WN9_NIHAOXIAOZHI_TTS=y", local)
        self.assertNotIn("CONFIG_CUSTOM_WAKE_WORD_SECONDARY", local)
        self.assertRegex(
            self.application_source,
            r"kWaitForSpeechTimeoutTicks\s*=\s*10",
        )
        self.assertRegex(
            self.application_source,
            r"kMaximumSpeechDurationTicks\s*=\s*20",
        )

    def test_afe_engine_respects_configured_wake_detector_type(self):
        engine = (ROOT / "main/audio/engines/afe_audio_engine.cc").read_text(
            encoding="utf-8"
        )
        self.assertIn("kUseCustomWakeWord && multinet_model_name != nullptr", engine)
        self.assertIn("kUseAfeWakeWord && wakenet_model_name != nullptr", engine)

    def test_legacy_tts_stop_honors_one_shot_mode(self):
        start = self.application_source.index(
            "// Official xiaozhi servers do not provide reply_id"
        )
        end = self.application_source.index(
            '} else if (strcmp(state->valuestring, "sentence_start") == 0)', start
        )
        legacy_stop = self.application_source[start:end]

        one_shot = re.search(
            r"#if CONFIG_HENSUN_ONE_SHOT_CONVERSATION(.*?)#else",
            legacy_stop,
            re.DOTALL,
        )
        self.assertIsNotNone(one_shot)
        self.assertIn("protocol_->CloseAudioChannel()", one_shot.group(1))
        self.assertIn("SetDeviceState(kDeviceStateIdle)", one_shot.group(1))

    def test_hensun_cam_uses_noise_tolerant_vad_settings(self):
        self.assertIn(
            "#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1",
            self.audio_engine_source,
        )
        self.assertIn("afe_config->vad_mode = VAD_MODE_3", self.audio_engine_source)
        self.assertIn(
            "afe_config->vad_min_noise_ms = 700",
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

    def test_camera_is_deferred_until_an_explicit_capture(self):
        self.assertIn("class HensunLazyCamera final : public Camera", self.source)
        self.assertIn("Starting camera for an explicit capture request", self.source)
        self.assertIn("return EnsureCamera() && camera_->Capture();", self.source)
        self.assertIn("camera_ = new HensunLazyCamera(camera_config);", self.source)

    def test_hensun_i2s_microphone_uses_upper_16_bits_with_bounded_input_gain(self):
        codec = re.search(
            r"class HensunAudioCodecSimplex.*?\n\};",
            self.source,
            re.DOTALL,
        )
        self.assertIsNotNone(codec)
        body = codec.group(0)
        self.assertRegex(body, r"int\s+Read\(int16_t\*\s+dest,\s*int\s+samples\)\s+override")
        self.assertIn("constexpr int kHensunMicInputGain = 1", self.source)
        self.assertIn("(bit32_buffer[index] >> 16) * kHensunMicInputGain", body)
        self.assertIn("value > INT16_MAX", body)
        self.assertNotIn("bit32_buffer[index] >> 12", body)

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
