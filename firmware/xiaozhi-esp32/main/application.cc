#include "application.h"
#include "assets.h"
#include "assets/lang_config.h"
#include "audio_codec.h"
#include "board.h"
#include "display.h"
#include "mcp_server.h"
#include "mqtt_protocol.h"
#include "settings.h"
#include "system_info.h"
#include "text_glyph_payload.h"
#include "websocket_protocol.h"

#include <driver/gpio.h>
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <arpa/inet.h>
#include <cJSON.h>
#include <cstring>

#define TAG "Application"

namespace {
// The Hensun CAM is simplex. Give the user time to begin, then separately
// bound an abnormal continuously-speaking VAD state.
constexpr int kWaitForSpeechTimeoutTicks = 10;
constexpr int kMaximumSpeechDurationTicks = 20;
constexpr int kReplyPendingTimeoutTicks = 12;
constexpr int64_t kReplySettleDurationUs = 800 * 1000;
constexpr int64_t kTtsFirstPcmTimeoutUs = 6 * 1000 * 1000;
constexpr int kHeartbeatIntervalTicks = 15;
constexpr int kHeartbeatMissLimit = 3;
constexpr int kStandbyReconnectIntervalTicks = 15;
}

Application::Application() {
    event_group_ = xEventGroupCreate();

#if CONFIG_USE_DEVICE_AEC && CONFIG_USE_SERVER_AEC
#error "CONFIG_USE_DEVICE_AEC and CONFIG_USE_SERVER_AEC cannot be enabled at the same time"
#elif CONFIG_USE_DEVICE_AEC
    aec_mode_ = kAecOnDeviceSide;
#elif CONFIG_USE_SERVER_AEC
    aec_mode_ = kAecOnServerSide;
#else
    aec_mode_ = kAecOff;
#endif

    esp_timer_create_args_t clock_timer_args = {.callback =
                                                    [](void* arg) {
                                                        Application* app = (Application*)arg;
                                                        xEventGroupSetBits(app->event_group_,
                                                                           MAIN_EVENT_CLOCK_TICK);
                                                    },
                                                .arg = this,
                                                .dispatch_method = ESP_TIMER_TASK,
                                                .name = "clock_timer",
                                                .skip_unhandled_events = true};
    esp_timer_create(&clock_timer_args, &clock_timer_handle_);

    esp_timer_create_args_t post_playback_listen_timer_args = {
        .callback = [](void* arg) {
            Application* app = static_cast<Application*>(arg);
            xEventGroupSetBits(app->event_group_, MAIN_EVENT_POST_PLAYBACK_GUARD);
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "post_playback_listen_guard",
        .skip_unhandled_events = true,
    };
    esp_timer_create(&post_playback_listen_timer_args, &post_playback_listen_timer_handle_);

    esp_timer_create_args_t tts_first_pcm_timer_args = {
        .callback = [](void* arg) {
            Application* app = static_cast<Application*>(arg);
            xEventGroupSetBits(app->event_group_, MAIN_EVENT_TTS_FIRST_PCM_TIMEOUT);
        },
        .arg = this,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "tts_first_pcm_watchdog",
        .skip_unhandled_events = true,
    };
    esp_timer_create(&tts_first_pcm_timer_args, &tts_first_pcm_timer_handle_);
}

Application::~Application() {
    if (clock_timer_handle_ != nullptr) {
        esp_timer_stop(clock_timer_handle_);
        esp_timer_delete(clock_timer_handle_);
    }
    if (post_playback_listen_timer_handle_ != nullptr) {
        esp_timer_stop(post_playback_listen_timer_handle_);
        esp_timer_delete(post_playback_listen_timer_handle_);
    }
    if (tts_first_pcm_timer_handle_ != nullptr) {
        esp_timer_stop(tts_first_pcm_timer_handle_);
        esp_timer_delete(tts_first_pcm_timer_handle_);
    }
    vEventGroupDelete(event_group_);
}

bool Application::SetDeviceState(DeviceState state) { return state_machine_.TransitionTo(state); }

void Application::Initialize() {
    auto& board = Board::GetInstance();
    SetDeviceState(kDeviceStateStarting);

    // Setup the display
    auto display = board.GetDisplay();
    display->SetupUI();
    // Print board name/version info
    display->SetChatMessage("system", SystemInfo::GetUserAgent().c_str());

    // Setup the audio service
    auto codec = board.GetAudioCodec();
    audio_service_.Initialize(codec);
    audio_service_.Start();

    AudioServiceCallbacks callbacks;
    callbacks.on_send_queue_available = [this]() {
        xEventGroupSetBits(event_group_, MAIN_EVENT_SEND_AUDIO);
    };
    callbacks.on_wake_word_detected = [this](const std::string& wake_word) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_WAKE_WORD_DETECTED);
    };
    callbacks.on_vad_change = [this](bool speaking) {
        if (speaking) {
            // The clock event and the scheduled VAD handler run on different
            // tasks. Latch the edge immediately so a 9.9-second utterance
            // cannot lose a race to the quiet-listening timeout.
            vad_speech_edge_pending_.store(true, std::memory_order_release);
        }
        Schedule([this, speaking]() {
            if (GetDeviceState() != kDeviceStateListening ||
                listening_mode_ != kListeningModeAutoStop) {
                vad_speech_edge_pending_.store(false, std::memory_order_release);
                return;
            }
            if (speaking) {
                vad_speech_detected_ = true;
                clock_ticks_ = 0;
            } else if (vad_speech_detected_) {
                vad_speech_detected_ = false;
                reply_pending_ = true;
                StopListening();
            }
            vad_speech_edge_pending_.store(false, std::memory_order_release);
        });
        xEventGroupSetBits(event_group_, MAIN_EVENT_VAD_CHANGE);
    };
    callbacks.on_playback_drained = [this]() {
        xEventGroupSetBits(event_group_, MAIN_EVENT_PLAYBACK_DRAINED);
    };
    callbacks.on_playback_started = [this]() {
        xEventGroupSetBits(event_group_, MAIN_EVENT_PLAYBACK_STARTED);
    };
    audio_service_.SetCallbacks(callbacks);

    // Add state change listeners
    state_machine_.AddStateChangeListener([this](DeviceState old_state, DeviceState new_state) {
        xEventGroupSetBits(event_group_, MAIN_EVENT_STATE_CHANGED);
    });

    // Start the clock timer to update the status bar
    esp_timer_start_periodic(clock_timer_handle_, 1000000);

    // Add MCP common tools (only once during initialization)
    auto& mcp_server = McpServer::GetInstance();
    mcp_server.AddCommonTools();
    mcp_server.AddUserOnlyTools();

    // Set network event callback for UI updates and network state handling
    board.SetNetworkEventCallback([this](NetworkEvent event, const std::string& data) {
        auto display = Board::GetInstance().GetDisplay();

        switch (event) {
            case NetworkEvent::Scanning:
                display->ShowNotification(Lang::Strings::SCANNING_WIFI, 30000);
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_DISCONNECTED);
                break;
            case NetworkEvent::Connecting: {
                if (data.empty()) {
                    // Cellular network - registering without carrier info yet
                    display->SetStatus(Lang::Strings::REGISTERING_NETWORK);
                } else {
                    // WiFi or cellular with carrier info
                    std::string msg = Lang::Strings::CONNECT_TO;
                    msg += data;
                    msg += "...";
                    display->ShowNotification(msg.c_str(), 30000);
                }
                break;
            }
            case NetworkEvent::Connected: {
                std::string msg = Lang::Strings::CONNECTED_TO;
                msg += data;
                display->ShowNotification(msg.c_str(), 30000);
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_CONNECTED);
                break;
            }
            case NetworkEvent::Disconnected:
                xEventGroupSetBits(event_group_, MAIN_EVENT_NETWORK_DISCONNECTED);
                break;
            case NetworkEvent::WifiConfigModeEnter:
                // WiFi config mode enter is handled by WifiBoard internally
                break;
            case NetworkEvent::WifiConfigModeExit:
                // WiFi config mode exit is handled by WifiBoard internally
                break;
            // Cellular modem specific events
            case NetworkEvent::ModemDetecting:
                display->SetStatus(Lang::Strings::DETECTING_MODULE);
                break;
            case NetworkEvent::ModemErrorNoSim:
                Alert(Lang::Strings::ERROR, Lang::Strings::PIN_ERROR, "warning",
                      Lang::Sounds::OGG_ERR_PIN);
                break;
            case NetworkEvent::ModemErrorRegDenied:
                Alert(Lang::Strings::ERROR, Lang::Strings::REG_ERROR, "warning",
                      Lang::Sounds::OGG_ERR_REG);
                break;
            case NetworkEvent::ModemErrorInitFailed:
                Alert(Lang::Strings::ERROR, Lang::Strings::MODEM_INIT_ERROR, "warning",
                      Lang::Sounds::OGG_EXCLAMATION);
                break;
            case NetworkEvent::ModemErrorTimeout:
                display->SetStatus(Lang::Strings::REGISTERING_NETWORK);
                break;
        }
    });

    // Start network asynchronously
    board.StartNetwork();

    // Update the status bar immediately to show the network state
    display->UpdateStatusBar(true);
}

void Application::Run() {
    // Set the priority of the main task to 10
    vTaskPrioritySet(nullptr, 10);

    const EventBits_t ALL_EVENTS =
        MAIN_EVENT_SCHEDULE | MAIN_EVENT_SEND_AUDIO | MAIN_EVENT_WAKE_WORD_DETECTED |
        MAIN_EVENT_VAD_CHANGE | MAIN_EVENT_CLOCK_TICK | MAIN_EVENT_ERROR |
        MAIN_EVENT_NETWORK_CONNECTED | MAIN_EVENT_NETWORK_DISCONNECTED | MAIN_EVENT_TOGGLE_CHAT |
        MAIN_EVENT_START_LISTENING | MAIN_EVENT_STOP_LISTENING | MAIN_EVENT_ACTIVATION_DONE |
        MAIN_EVENT_STATE_CHANGED | MAIN_EVENT_PLAYBACK_DRAINED |
        MAIN_EVENT_POST_PLAYBACK_GUARD | MAIN_EVENT_PLAYBACK_STARTED |
        MAIN_EVENT_TTS_FIRST_PCM_TIMEOUT;

    while (true) {
        auto bits = xEventGroupWaitBits(event_group_, ALL_EVENTS, pdTRUE, pdFALSE, portMAX_DELAY);

        if (bits & MAIN_EVENT_ERROR) {
            AbortDialogueToStandby("network-error", true);
            Alert(Lang::Strings::ERROR, last_error_message_.c_str(), "cancel",
                  Lang::Sounds::OGG_EXCLAMATION);
        }

        if (bits & MAIN_EVENT_NETWORK_CONNECTED) {
            HandleNetworkConnectedEvent();
        }

        if (bits & MAIN_EVENT_NETWORK_DISCONNECTED) {
            HandleNetworkDisconnectedEvent();
        }

        if (bits & MAIN_EVENT_ACTIVATION_DONE) {
            HandleActivationDoneEvent();
        }

        if (bits & MAIN_EVENT_STATE_CHANGED) {
            HandleStateChangedEvent();
        }

        if (bits & MAIN_EVENT_PLAYBACK_DRAINED) {
            if (!pending_tts_stop_reply_id_.empty() && audio_service_.IsPlaybackIdle()) {
                FinishTtsPlayback(pending_tts_stop_reply_id_);
            } else if (!post_playback_guard_active_ && pending_listening_start_ &&
                GetDeviceState() == kDeviceStateListening &&
                audio_service_.IsPlaybackIdle()) {
                // Deferred legacy listening start (auto mode).
                pending_listening_start_ = false;
                StartListeningAudio();
            }
        }

        if (bits & MAIN_EVENT_PLAYBACK_STARTED) {
            if (tts_playback_prepared_.load() && !tts_audio_started_ &&
                !active_tts_reply_id_.empty()) {
                CancelTtsFirstPcmWatchdog();
                tts_audio_started_ = true;
                reply_pending_ = false;
                SetDeviceState(kDeviceStateSpeaking);
#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 || CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1
                if (protocol_) {
                    protocol_->SendDeviceStage(
                        "speaker_pcm_started", active_turn_id_, active_tts_reply_id_);
                }
#endif
            }
        }

        if (bits & MAIN_EVENT_TTS_FIRST_PCM_TIMEOUT) {
            if (tts_playback_prepared_.load() && !tts_audio_started_ &&
                !active_tts_reply_id_.empty()) {
                ESP_LOGE(TAG, "tts-first-pcm-timeout");
                AbortDialogueToStandby("tts-first-pcm-timeout", true);
            }
        }

        if (bits & MAIN_EVENT_POST_PLAYBACK_GUARD) {
            if (reply_settle_active_) {
                const bool resume_listening = post_playback_guard_active_;
                reply_settle_active_ = false;
                post_playback_guard_active_ = false;
                Board::GetInstance().GetDisplay()->CompleteReplySettle();
                if (resume_listening && GetDeviceState() == kDeviceStateIdle) {
                    SetDeviceState(kDeviceStateListening);
                } else if (GetDeviceState() == kDeviceStateIdle) {
                    Board::GetInstance().GetDisplay()->SetStatus(Lang::Strings::STANDBY);
                }
            }
            if (pending_listening_start_ && GetDeviceState() == kDeviceStateListening &&
                audio_service_.IsPlaybackIdle()) {
                pending_listening_start_ = false;
                StartListeningAudio();
            }
        }

        if (bits & MAIN_EVENT_TOGGLE_CHAT) {
            HandleToggleChatEvent();
        }

        if (bits & MAIN_EVENT_START_LISTENING) {
            HandleStartListeningEvent();
        }

        if (bits & MAIN_EVENT_STOP_LISTENING) {
            HandleStopListeningEvent();
        }

        if (bits & MAIN_EVENT_SEND_AUDIO) {
            // While Bootstrap/WSS is connecting, keep the encoded utterance in
            // order. StartListeningAudio() sends listen.start before it re-arms
            // this event, so no audio can reach the server prematurely.
            const auto state = GetDeviceState();
            const bool upload_ready = protocol_ && protocol_->IsAudioChannelOpened() &&
                                      listening_capture_ready_;
            if (!upload_ready && state != kDeviceStateConnecting &&
                state != kDeviceStateListening) {
                audio_service_.DiscardSendQueue();
            } else if (upload_ready) {
                while (auto packet = audio_service_.PopPacketFromSendQueue()) {
                    if (!protocol_->SendAudio(std::move(packet))) {
                        // Drop the remaining packets. Leaving them in the queue would
                        // stall the Opus codec task (it waits for queue space), which in
                        // turn deadlocks the whole audio input pipeline, as no new
                        // MAIN_EVENT_SEND_AUDIO event would ever be triggered again.
                        audio_service_.DiscardSendQueue();
                        break;
                    }
                }
            }
        }

        if (bits & MAIN_EVENT_WAKE_WORD_DETECTED) {
            HandleWakeWordDetectedEvent();
        }

        if (bits & MAIN_EVENT_VAD_CHANGE) {
            if (GetDeviceState() == kDeviceStateListening) {
                auto led = Board::GetInstance().GetLed();
                led->OnStateChanged();
            }
        }

        if (bits & MAIN_EVENT_SCHEDULE) {
            std::unique_lock<std::mutex> lock(mutex_);
            auto tasks = std::move(main_tasks_);
            lock.unlock();
            for (auto& task : tasks) {
                task();
            }
        }

        if (bits & MAIN_EVENT_CLOCK_TICK) {
            clock_ticks_++;
            auto display = Board::GetInstance().GetDisplay();
            display->UpdateStatusBar();

            if (protocol_ && protocol_->IsAudioChannelOpened() &&
                protocol_->SupportsHeartbeat()) {
                heartbeat_ticks_++;
                if (heartbeat_ticks_ >= kHeartbeatIntervalTicks) {
                    heartbeat_ticks_ = 0;
                    if (heartbeat_awaiting_ != 0) {
                        heartbeat_missed_++;
                    }
                    if (heartbeat_missed_ >= kHeartbeatMissLimit) {
                        ESP_LOGW(TAG, "Device heartbeat timed out after %d missed replies",
                                 heartbeat_missed_);
                        AbortDialogueToStandby("heartbeat-timeout", true);
                    } else {
                        heartbeat_sequence_++;
                        if (heartbeat_sequence_ == 0) {
                            heartbeat_sequence_ = 1;
                        }
                        heartbeat_awaiting_ = heartbeat_sequence_;
                        if (!protocol_->SendHeartbeat(heartbeat_sequence_)) {
                            AbortDialogueToStandby("heartbeat-send-failed", true);
                        }
                    }
                }
            } else {
                heartbeat_ticks_ = 0;
                heartbeat_missed_ = 0;
                heartbeat_awaiting_ = 0;
            }

#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 && !CONFIG_HENSUN_ONE_SHOT_CONVERSATION
            if (GetDeviceState() == kDeviceStateIdle && protocol_ &&
                !protocol_->IsAudioChannelOpened()) {
                standby_reconnect_ticks_++;
                if (standby_reconnect_ticks_ >= kStandbyReconnectIntervalTicks) {
                    standby_reconnect_ticks_ = 0;
                    EnsureControlChannelReady();
                }
            } else {
                standby_reconnect_ticks_ = 0;
            }
#endif

            // Waiting for speech and recording an active utterance use separate
            // deadlines. A quiet follow-up window is cancelled without asking
            // ASR to transcribe silence.
            if (GetDeviceState() == kDeviceStateListening &&
                listening_capture_ready_ &&
                listening_mode_ == kListeningModeAutoStop) {
                if (!vad_speech_detected_ &&
                    !vad_speech_edge_pending_.load(std::memory_order_acquire) &&
                    clock_ticks_ >= kWaitForSpeechTimeoutTicks) {
                    ESP_LOGI(TAG, "No follow-up speech; returning to standby");
                    AbortSpeaking(kAbortReasonNone);
                    SetDeviceState(kDeviceStateIdle);
                } else if (vad_speech_detected_ &&
                           clock_ticks_ >= kMaximumSpeechDurationTicks) {
                    ESP_LOGW(TAG, "Maximum utterance duration reached");
                    reply_pending_ = true;
                    StopListening();
                }
            }

            if (GetDeviceState() == kDeviceStateIdle &&
                reply_pending_ && clock_ticks_ >= kReplyPendingTimeoutTicks) {
                AbortDialogueToStandby("reply-timeout", true);
            }

            // Print debug info every 10 seconds
            if (clock_ticks_ % 10 == 0) {
                SystemInfo::PrintHeapStats();
                // SystemInfo::PrintTaskList();
                // SystemInfo::PrintTaskCpuUsage(pdMS_TO_TICKS(1000));
            }
        }
    }
}

void Application::HandleNetworkConnectedEvent() {
    ESP_LOGI(TAG, "Network connected");
    auto state = GetDeviceState();

    if (state == kDeviceStateStarting || state == kDeviceStateWifiConfiguring) {
        // Network is ready, start activation
        SetDeviceState(kDeviceStateActivating);
        if (activation_task_handle_ != nullptr) {
            ESP_LOGW(TAG, "Activation task already running");
            return;
        }

        xTaskCreate(
            [](void* arg) {
                Application* app = static_cast<Application*>(arg);
                app->ActivationTask();
                app->activation_task_handle_ = nullptr;
                vTaskDelete(NULL);
            },
            "activation", 4096 * 2, this, 2, &activation_task_handle_);
    }
#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 && !CONFIG_HENSUN_ONE_SHOT_CONVERSATION
    else if (state == kDeviceStateIdle) {
        EnsureControlChannelReady();
    }
#endif

    // Update the status bar immediately to show the network state
    auto display = Board::GetInstance().GetDisplay();
    display->UpdateStatusBar(true);
}

void Application::HandleNetworkDisconnectedEvent() {
    // Close current conversation when network disconnected
    auto state = GetDeviceState();
    if (state == kDeviceStateConnecting || state == kDeviceStateListening ||
        state == kDeviceStateSpeaking || reply_pending_ ||
        tts_playback_prepared_.load() || !active_turn_id_.empty() ||
        !active_tts_reply_id_.empty()) {
        AbortDialogueToStandby("network-disconnected", false);
    }

    // Update the status bar immediately to show the network state
    auto display = Board::GetInstance().GetDisplay();
    display->UpdateStatusBar(true);
}

void Application::HandleActivationDoneEvent() {
    ESP_LOGI(TAG, "Activation done");

    SystemInfo::PrintHeapStats();
    SetDeviceState(kDeviceStateIdle);

    has_server_time_ = ota_->HasServerTime();

    auto display = Board::GetInstance().GetDisplay();
    std::string message = std::string(Lang::Strings::VERSION) + ota_->GetCurrentVersion();
    display->ShowNotification(message.c_str());
    display->SetChatMessage("system", "");

    // Release OTA object after activation is complete
    ota_.reset();
    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);

    Schedule([this]() {
        // Play the success sound to indicate the device is ready
        audio_service_.PlaySound(Lang::Sounds::OGG_SUCCESS);
    });
#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 && !CONFIG_HENSUN_ONE_SHOT_CONVERSATION
    EnsureControlChannelReady();
#endif
#if CONFIG_HENSUN_DIAGNOSTIC_AUTO_LISTEN_ON_BOOT
    xTaskCreate(
        [](void* arg) {
            auto* app = static_cast<Application*>(arg);
            ESP_LOGW(TAG, "Diagnostic auto-listen armed; starting every 60 seconds");
            for (int attempt = 1; attempt <= 3; ++attempt) {
                vTaskDelay(pdMS_TO_TICKS(60000));
                ESP_LOGW(TAG, "Diagnostic auto-listen firing attempt=%d state=%d",
                         attempt, static_cast<int>(app->GetDeviceState()));
                app->StartListening();
            }
            vTaskDelete(nullptr);
        },
        "diag_auto_listen", 4096, this, 1, nullptr);
#endif
}

void Application::EnsureControlChannelReady() {
    if (!protocol_ || protocol_->IsAudioChannelOpened() ||
        control_channel_task_handle_ != nullptr) {
        return;
    }

    BaseType_t created = xTaskCreate(
        [](void* arg) {
            auto* app = static_cast<Application*>(arg);
            // Idle preconnect only reuses the already provisioned WSS config.
            // A token refresh can replace the Protocol object, so it remains a
            // main-loop operation on the next real wake instead of racing this
            // background task.
            const bool opened = app->protocol_->OpenAudioChannel();
            app->control_channel_task_handle_ = nullptr;
            app->Schedule([app, opened]() {
                if (!opened) {
                    ESP_LOGW(TAG, "Idle control channel preconnect failed");
                }
                if (app->GetDeviceState() != kDeviceStateConnecting) {
                    return;
                }
                if (!app->pending_connect_wake_word_.empty()) {
                    const std::string wake_word = app->pending_connect_wake_word_;
                    app->ContinueWakeWordInvoke(wake_word);
                } else {
                    app->ContinueOpenAudioChannel(kListeningModeManualStop);
                }
            });
            vTaskDelete(nullptr);
        },
        "control_channel", 4096 * 2, this, 2, &control_channel_task_handle_);
    if (created != pdPASS) {
        control_channel_task_handle_ = nullptr;
        ESP_LOGE(TAG, "Failed to create idle control channel task");
    }
}

void Application::ActivationTask() {
    // Create OTA object for activation process
    ota_ = std::make_unique<Ota>();

    // Check for new assets version
    CheckAssetsVersion();

    // Check for new firmware version
    CheckNewVersion();

    // Initialize the protocol
    InitializeProtocol();

    // Signal completion to main loop
    xEventGroupSetBits(event_group_, MAIN_EVENT_ACTIVATION_DONE);
}

void Application::CheckAssetsVersion() {
    // Only allow CheckAssetsVersion to be called once
    if (assets_version_checked_) {
        return;
    }
    assets_version_checked_ = true;

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto& assets = Assets::GetInstance();

    if (!assets.partition_valid()) {
        ESP_LOGW(TAG, "Assets partition is disabled for board %s", BOARD_NAME);
        return;
    }

    Settings settings("assets", true);
    // Check if there is a new assets need to be downloaded
    std::string download_url = settings.GetString("download_url");

    if (!download_url.empty()) {
        settings.EraseKey("download_url");

        char message[256];
        snprintf(message, sizeof(message), Lang::Strings::FOUND_NEW_ASSETS, download_url.c_str());
        Alert(Lang::Strings::LOADING_ASSETS, message, "cloud_download", Lang::Sounds::OGG_UPGRADE);

        // Wait for the audio service to be idle for 3 seconds
        vTaskDelay(pdMS_TO_TICKS(3000));
        SetDeviceState(kDeviceStateUpgrading);
        board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
        display->SetChatMessage("system", Lang::Strings::PLEASE_WAIT);

        bool success =
            assets.Download(download_url, [this, display](int progress, size_t speed) -> void {
                char buffer[32];
                snprintf(buffer, sizeof(buffer), "%d%% %uKB/s", progress, speed / 1024);
                Schedule([display, message = std::string(buffer)]() {
                    display->SetChatMessage("system", message.c_str());
                });
            });

        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        vTaskDelay(pdMS_TO_TICKS(1000));

        if (!success) {
            Alert(Lang::Strings::ERROR, Lang::Strings::DOWNLOAD_ASSETS_FAILED, "cancel",
                  Lang::Sounds::OGG_EXCLAMATION);
            vTaskDelay(pdMS_TO_TICKS(2000));
            SetDeviceState(kDeviceStateActivating);
            return;
        }
    }

    // Apply assets
    assets.Apply();
    display->SetChatMessage("system", "");
    display->SetEmotion("robot_2");
}

void Application::CheckNewVersion() {
    const int MAX_RETRY = 10;
    int retry_count = 0;
    int retry_delay = 10;  // Initial retry delay in seconds
    std::string announced_activation_code;
    bool activation_prompt_visible = false;

    auto& board = Board::GetInstance();
    while (true) {
        auto display = board.GetDisplay();
        if (!activation_prompt_visible) {
            display->SetStatus(Lang::Strings::CHECKING_NEW_VERSION);
        }

        esp_err_t err = ota_->CheckVersion();
        if (err != ESP_OK) {
            activation_prompt_visible = false;
            retry_count++;
            if (retry_count >= MAX_RETRY) {
                ESP_LOGE(TAG, "Too many retries, exit version check");
                return;
            }

            char error_message[128];
            snprintf(error_message, sizeof(error_message), "code=%d, url=%s", err,
                     ota_->GetCheckVersionUrl().c_str());
            char buffer[256];
            snprintf(buffer, sizeof(buffer), Lang::Strings::CHECK_NEW_VERSION_FAILED, retry_delay,
                     error_message);
            Alert(Lang::Strings::ERROR, buffer, "cloud_off", Lang::Sounds::OGG_EXCLAMATION);

            ESP_LOGW(TAG, "Check new version failed, retry in %d seconds (%d/%d)", retry_delay,
                     retry_count, MAX_RETRY);
            for (int i = 0; i < retry_delay; i++) {
                vTaskDelay(pdMS_TO_TICKS(1000));
                if (GetDeviceState() == kDeviceStateIdle) {
                    break;
                }
            }
            retry_delay *= 2;  // Double the retry delay
            continue;
        }
        retry_count = 0;
        retry_delay = 10;  // Reset retry delay

        if (ota_->HasNewVersion()) {
            activation_prompt_visible = false;
            if (UpgradeFirmware(ota_->GetFirmwareUrl(), ota_->GetFirmwareVersion())) {
                return;  // This line will never be reached after reboot
            }
            // If upgrade failed, continue to normal operation
        }

        // No new version, mark the current version as valid
        ota_->MarkCurrentVersionValid();
        if (!ota_->HasActivationCode() && !ota_->HasActivationChallenge()) {
            // Exit the loop if done checking new version
            break;
        }

        // Activation code is shown to the user and waiting for the user to input
        if (ota_->HasActivationCode()) {
            const auto& code = ota_->GetActivationCode();
            if (code != announced_activation_code) {
                ShowActivationCode(code, ota_->GetActivationMessage());
                announced_activation_code = code;
            } else if (!activation_prompt_visible) {
                // Restore the QR after an error without replaying the same code.
                display->SetStatus(Lang::Strings::ACTIVATION);
                display->ShowActivationCode(code.c_str(), ota_->GetActivationClaimUrl().c_str());
            }
            activation_prompt_visible = true;
        } else {
            activation_prompt_visible = false;
            display->SetStatus(Lang::Strings::ACTIVATION);
        }

        // Code-only claiming is confirmed by the next bootstrap, not Activate().
        if (!ota_->HasActivationChallenge()) {
            vTaskDelay(pdMS_TO_TICKS(3000));
            continue;
        }

        // This will block the loop until the activation is done or timeout
        for (int i = 0; i < 10; ++i) {
            ESP_LOGI(TAG, "Activating... %d/%d", i + 1, 10);
            esp_err_t err = ota_->Activate();
            if (err == ESP_OK) {
                break;
            } else if (err == ESP_ERR_TIMEOUT) {
                vTaskDelay(pdMS_TO_TICKS(3000));
            } else {
                vTaskDelay(pdMS_TO_TICKS(10000));
            }
            if (GetDeviceState() == kDeviceStateIdle) {
                break;
            }
        }
    }
}

void Application::InitializeProtocol() {
    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto codec = board.GetAudioCodec();

    display->SetStatus(Lang::Strings::LOADING_PROTOCOL);

    if (ota_->HasMqttConfig()) {
        protocol_ = std::make_unique<MqttProtocol>();
    } else if (ota_->HasWebsocketConfig()) {
        protocol_ = std::make_unique<WebsocketProtocol>();
    } else {
        ESP_LOGW(TAG, "No protocol specified in the OTA config, using MQTT");
        protocol_ = std::make_unique<MqttProtocol>();
    }

    protocol_->OnConnected([this]() { DismissAlert(); });

    protocol_->OnNetworkError([this](const std::string& message) {
        last_error_message_ = message;
        xEventGroupSetBits(event_group_, MAIN_EVENT_ERROR);
    });

    protocol_->OnIncomingAudio([this](std::unique_ptr<AudioStreamPacket> packet) {
        if (GetDeviceState() == kDeviceStateSpeaking || tts_playback_prepared_.load()) {
            if (!audio_service_.PushPacketToDecodeQueue(std::move(packet), true)) {
                ESP_LOGE(TAG, "Playback queue rejected a packet after backpressure wait");
            }
        }

    });

    protocol_->OnAudioChannelOpened([this, codec, &board]() {
        board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
        heartbeat_ticks_ = 0;
        heartbeat_missed_ = 0;
        heartbeat_awaiting_ = 0;
        if (protocol_->server_sample_rate() != codec->output_sample_rate()) {
            ESP_LOGW(TAG,
                     "Server sample rate %d does not match device output sample rate %d, "
                     "resampling may cause distortion",
                     protocol_->server_sample_rate(), codec->output_sample_rate());
        }
    });

    protocol_->OnAudioChannelClosed([this, &board]() {
        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);
        Schedule([this]() {
            heartbeat_ticks_ = 0;
            heartbeat_missed_ = 0;
            heartbeat_awaiting_ = 0;
            if (GetDeviceState() == kDeviceStateIdle && !reply_pending_ &&
                !tts_playback_prepared_.load() && active_tts_reply_id_.empty()) {
                ESP_LOGI(TAG, "Ignoring audio channel close after standby");
                return;
            }
            AbortDialogueToStandby("audio-channel-closed", false);
        });
    });

    protocol_->OnIncomingJson([this, display](const cJSON* root) {
        // Parse JSON data
        auto type = cJSON_GetObjectItem(root, "type");
        if (!cJSON_IsString(type)) {
            ESP_LOGW(TAG, "Incoming JSON message has no type");
            return;
        }
        if (strcmp(type->valuestring, "pong") == 0) {
            auto sequence = cJSON_GetObjectItem(root, "sequence");
            if (cJSON_IsNumber(sequence) && sequence->valuedouble >= 0) {
                uint32_t pong_sequence = static_cast<uint32_t>(sequence->valuedouble);
                Schedule([this, pong_sequence]() {
                    if (heartbeat_awaiting_ == pong_sequence) {
                        heartbeat_awaiting_ = 0;
                        heartbeat_missed_ = 0;
                    }
                });
            }
        } else if (strcmp(type->valuestring, "tts") == 0) {
            auto state = cJSON_GetObjectItem(root, "state");
            if (!cJSON_IsString(state)) {
                return;
            }
            if (strcmp(state->valuestring, "start") == 0) {
                auto reply_id = cJSON_GetObjectItem(root, "reply_id");
                auto turn_id = cJSON_GetObjectItem(root, "turn_id");
                std::string playback_reply_id =
                    cJSON_IsString(reply_id) ? reply_id->valuestring : "";
                std::string playback_turn_id =
                    cJSON_IsString(turn_id) ? turn_id->valuestring : "";
                Schedule([this, playback_reply_id, playback_turn_id]() {
                    if (!playback_turn_id.empty() && !active_turn_id_.empty() &&
                        playback_turn_id != active_turn_id_ &&
                        GetDeviceState() == kDeviceStateSpeaking) {
                        ESP_LOGW(TAG, "Ignoring stale TTS start for completed turn");
                        return;
                    }
                    if (!playback_turn_id.empty()) {
                        active_turn_id_ = playback_turn_id;
                    }
                    post_playback_guard_active_ = false;
                    reply_settle_active_ = false;
                    pending_listening_start_ = false;
                    if (post_playback_listen_timer_handle_ != nullptr) {
                        esp_timer_stop(post_playback_listen_timer_handle_);
                    }
                    aborted_ = false;
                    active_tts_reply_id_ = playback_reply_id;
                    pending_tts_stop_reply_id_.clear();
                    tts_audio_started_ = false;
                    audio_service_.ResetDecoder();
                    tts_playback_prepared_.store(true);
                    if (!active_tts_reply_id_.empty() && protocol_) {
                        if (!protocol_->SendTtsState(
                                "ready", active_tts_reply_id_, active_turn_id_)) {
                            ESP_LOGE(TAG, "tts-ready-send-failed");
                            AbortDialogueToStandby("tts-ready-send-failed", true);
                            return;
                        }
                        StartTtsFirstPcmWatchdog();
                    } else {
                        // Official servers do not use the ready handshake. Keep
                        // their legacy start-immediately behavior.
                        reply_pending_ = false;
                        SetDeviceState(kDeviceStateSpeaking);
                    }
                });
            } else if (strcmp(state->valuestring, "stop") == 0) {
                auto reply_id = cJSON_GetObjectItem(root, "reply_id");
                auto turn_id = cJSON_GetObjectItem(root, "turn_id");
                if (cJSON_IsString(reply_id)) {
                    std::string playback_reply_id = reply_id->valuestring;
                    std::string playback_turn_id =
                        cJSON_IsString(turn_id) ? turn_id->valuestring : "";
                    Schedule([this, playback_reply_id, playback_turn_id]() {
                        if (!playback_turn_id.empty() && !active_turn_id_.empty() &&
                            playback_turn_id != active_turn_id_) {
                            ESP_LOGW(TAG, "Ignoring stale TTS stop for completed turn");
                            return;
                        }
                        if (playback_reply_id != active_tts_reply_id_) {
                            ESP_LOGW(TAG, "Ignoring stale TTS stop acknowledgement request");
                            return;
                        }
                        CancelTtsFirstPcmWatchdog();
                        pending_tts_stop_reply_id_ = playback_reply_id;
                        if (audio_service_.IsPlaybackIdle()) {
                            FinishTtsPlayback(playback_reply_id);
                        }
                    });
                } else {
                    // Official xiaozhi servers do not provide reply_id; retain
                    // their immediate-stop behavior for protocol compatibility.
                    Schedule([this]() {
                        CancelTtsFirstPcmWatchdog();
                        tts_playback_prepared_.store(false);
                        tts_audio_started_ = false;
                        if (GetDeviceState() == kDeviceStateSpeaking) {
#if CONFIG_HENSUN_ONE_SHOT_CONVERSATION
                            protocol_->CloseAudioChannel();
                            SetDeviceState(kDeviceStateIdle);
#else
                            if (listening_mode_ == kListeningModeManualStop) {
                                SetDeviceState(kDeviceStateIdle);
                            } else {
                                SetDeviceState(kDeviceStateListening);
                            }
#endif
                        }
                    });
                }
            } else if (strcmp(state->valuestring, "sentence_start") == 0) {
                auto text = cJSON_GetObjectItem(root, "text");
                if (cJSON_IsString(text)) {
                    std::vector<TextGlyph> glyphs;
                    uint8_t bpp = 0;
                    if (!TextGlyphPayload::Parse(root, glyphs, bpp)) {
                        glyphs.clear();
                    }
                    ESP_LOGI(TAG, "assistant text received (%u bytes)",
                             static_cast<unsigned>(std::strlen(text->valuestring)));
                    Schedule([display, message = std::string(text->valuestring),
                              glyphs = std::move(glyphs), bpp]() {
                        display->AddTextGlyphs(glyphs, bpp);
                        display->SetChatMessage("assistant", message.c_str());
                    });
                }
            }
        } else if (strcmp(type->valuestring, "stt") == 0) {
            auto text = cJSON_GetObjectItem(root, "text");
            auto turn_id = cJSON_GetObjectItem(root, "turn_id");
            if (cJSON_IsString(text)) {
                std::vector<TextGlyph> glyphs;
                uint8_t bpp = 0;
                if (!TextGlyphPayload::Parse(root, glyphs, bpp)) {
                    glyphs.clear();
                }
                ESP_LOGI(TAG, "user text received (%u bytes)",
                         static_cast<unsigned>(std::strlen(text->valuestring)));
                Schedule([this, display, message = std::string(text->valuestring),
                          turn_id = cJSON_IsString(turn_id) ? std::string(turn_id->valuestring) : "",
                          glyphs = std::move(glyphs), bpp]() {
                    if (!turn_id.empty()) {
                        active_turn_id_ = turn_id;
                    }
                    display->AddTextGlyphs(glyphs, bpp);
                    display->SetChatMessage("user", message.c_str());
                });
            }
        } else if (strcmp(type->valuestring, "llm") == 0) {
            auto emotion = cJSON_GetObjectItem(root, "emotion");
            auto turn_id = cJSON_GetObjectItem(root, "turn_id");
            if (cJSON_IsString(emotion)) {
                Schedule([this, display, emotion_str = std::string(emotion->valuestring),
                          turn_id = cJSON_IsString(turn_id) ? std::string(turn_id->valuestring) : ""]() {
                    const bool stale_turn = !turn_id.empty() &&
                                            turn_id != active_turn_id_;
                    const bool standby_without_reply =
                        GetDeviceState() == kDeviceStateIdle &&
                        !reply_pending_ && !tts_playback_prepared_.load() &&
                        active_tts_reply_id_.empty();
                    if (stale_turn || standby_without_reply) {
                        ESP_LOGW(TAG, "Ignoring stale LLM emotion for completed turn");
                        return;
                    }
                    display->SetEmotion(emotion_str.c_str());
                });
            }
        } else if (strcmp(type->valuestring, "error") == 0) {
            auto code = cJSON_GetObjectItem(root, "code");
            std::string reason = cJSON_IsString(code) ? code->valuestring : "gateway-error";
            Schedule([this, reason]() {
                AbortDialogueToStandby(reason.c_str(), true);
            });
        } else if (strcmp(type->valuestring, "listen") == 0) {
            auto state = cJSON_GetObjectItem(root, "state");
            if (!cJSON_IsString(state)) {
                return;
            }
            if (strcmp(state->valuestring, "resume") == 0) {
                Schedule([this]() {
                    if (tts_playback_prepared_.load() || !active_tts_reply_id_.empty() ||
                        GetDeviceState() == kDeviceStateSpeaking ||
                        !audio_service_.IsPlaybackIdle()) {
                        ESP_LOGW(TAG, "Ignoring listen resume while playback is active");
                        return;
                    }
                    if (GetDeviceState() == kDeviceStateListening) {
                        return;
                    }
                    if (GetDeviceState() != kDeviceStateIdle) {
                        ESP_LOGW(TAG, "Ignoring listen resume outside idle state");
                        return;
                    }
                    post_playback_guard_active_ = false;
                    reply_settle_active_ = false;
                    pending_listening_start_ = false;
                    if (post_playback_listen_timer_handle_ != nullptr) {
                        esp_timer_stop(post_playback_listen_timer_handle_);
                    }
                    SetListeningMode(GetDefaultListeningMode());
                });
            } else if (strcmp(state->valuestring, "standby") == 0) {
                auto reason = cJSON_GetObjectItem(root, "reason");
                std::string reason_str =
                    cJSON_IsString(reason) ? reason->valuestring : "asr-no-speech";
                Schedule([this, reason_str]() {
                    const bool user_exit = reason_str == "user-exit";
                    AbortDialogueToStandby(
                        user_exit ? "user-exit" : "asr-no-speech", true);
                });
            }
        } else if (strcmp(type->valuestring, "mcp") == 0) {
            auto payload = cJSON_GetObjectItem(root, "payload");
            if (cJSON_IsObject(payload)) {
                McpServer::GetInstance().ParseMessage(payload);
            }
        } else if (strcmp(type->valuestring, "system") == 0) {
            auto command = cJSON_GetObjectItem(root, "command");
            if (cJSON_IsString(command)) {
                ESP_LOGI(TAG, "System command: %s", command->valuestring);
                if (strcmp(command->valuestring, "reboot") == 0) {
                    // Do a reboot if user requests a OTA update
                    Schedule([this]() { Reboot(); });
                } else if (strcmp(command->valuestring, "apply_config") == 0) {
                    HandleDeviceConfig(root);
                } else {
                    ESP_LOGW(TAG, "Unknown system command: %s", command->valuestring);
                }
            }
        } else if (strcmp(type->valuestring, "alert") == 0) {
            auto status = cJSON_GetObjectItem(root, "status");
            auto message = cJSON_GetObjectItem(root, "message");
            auto emotion = cJSON_GetObjectItem(root, "emotion");
            if (cJSON_IsString(status) && cJSON_IsString(message) && cJSON_IsString(emotion)) {
                Alert(status->valuestring, message->valuestring, emotion->valuestring,
                      Lang::Sounds::OGG_VIBRATION);
            } else {
                ESP_LOGW(TAG, "Alert command requires status, message and emotion");
            }
#if CONFIG_RECEIVE_CUSTOM_MESSAGE
        } else if (strcmp(type->valuestring, "custom") == 0) {
            auto payload = cJSON_GetObjectItem(root, "payload");
            ESP_LOGI(TAG, "Received custom message: %s", cJSON_PrintUnformatted(root));
            if (cJSON_IsObject(payload)) {
                Schedule(
                    [this, display, payload_str = std::string(cJSON_PrintUnformatted(payload))]() {
                        display->SetChatMessage("system", payload_str.c_str());
                    });
            } else {
                ESP_LOGW(TAG, "Invalid custom message format: missing payload");
            }
#endif
        } else {
            ESP_LOGW(TAG, "Unknown message type: %s", type->valuestring);
        }
    });

    protocol_->Start();
}

void Application::HandleDeviceConfig(const cJSON* root) {
    auto command_id = cJSON_GetObjectItem(root, "command_id");
    auto config_version = cJSON_GetObjectItem(root, "config_version");
    auto config = cJSON_GetObjectItem(root, "config");
    auto speaker_volume = cJSON_IsObject(config)
                              ? cJSON_GetObjectItem(config, "speaker_volume")
                              : nullptr;
    auto screen_brightness = cJSON_IsObject(config)
                                 ? cJSON_GetObjectItem(config, "screen_brightness")
                                 : nullptr;
    if (!cJSON_IsString(command_id) || !cJSON_IsNumber(config_version) ||
        !cJSON_IsNumber(speaker_volume) || !cJSON_IsNumber(screen_brightness)) {
        ESP_LOGW(TAG, "Device configuration command is malformed");
        return;
    }

    const int version = config_version->valueint;
    const int volume = speaker_volume->valueint;
    const int brightness = screen_brightness->valueint;
    const std::string id(command_id->valuestring);
    Settings saved("hensun_config");
    const int saved_version = saved.GetInt("version", 0);
    if (version < saved_version) {
        protocol_->SendDeviceConfigAck(id, version, false, 0, 0, "stale-version");
        return;
    }
    if (version < 1 || volume < 10 || volume > 100 || brightness < 10 ||
        brightness > 100) {
        protocol_->SendDeviceConfigAck(id, version, false, 0, 0, "invalid-config");
        return;
    }

    Schedule([this, id, version, volume, brightness]() {
        auto& board = Board::GetInstance();
        auto codec = board.GetAudioCodec();
        auto backlight = board.GetBacklight();
        if (codec == nullptr || backlight == nullptr) {
            protocol_->SendDeviceConfigAck(
                id, version, false, 0, 0, "unsupported-hardware");
            return;
        }
        codec->SetOutputVolume(volume);
        backlight->SetBrightness(static_cast<uint8_t>(brightness), true);
        Settings settings("hensun_config", true);
        settings.SetInt("version", version);
        protocol_->SendDeviceConfigAck(id, version, true, volume, brightness);
        ESP_LOGI(TAG, "Applied device configuration v%d (volume=%d, brightness=%d)",
                 version, volume, brightness);
    });
}

bool Application::OpenAudioChannelWithConfigRefresh() {
    ESP_LOGI(TAG, "Opening audio channel; protocol=%p", protocol_.get());
    // The cached device-session token is valid for a short period. Reuse it
    // first so an ordinary wake-up does not pay for a second HTTPS bootstrap
    // round trip. If it has expired, the failed WSS attempt falls through to a
    // fresh bootstrap below.
    if (protocol_ && protocol_->OpenAudioChannel()) {
        ESP_LOGI(TAG, "Audio channel opened using cached websocket configuration");
        last_error_message_.clear();
        xEventGroupClearBits(event_group_, MAIN_EVENT_ERROR);
        return true;
    }

    // Bootstrap is only needed on first connection, after token expiry, or
    // when the saved websocket configuration cannot reconnect.
    if (!ota_) {
        ota_ = std::make_unique<Ota>();
    }
    if (ota_->CheckVersion() == ESP_OK) {
        InitializeProtocol();
        if (protocol_ && protocol_->OpenAudioChannel()) {
            ESP_LOGI(TAG, "Audio channel opened after bootstrap refresh");
            last_error_message_.clear();
            xEventGroupClearBits(event_group_, MAIN_EVENT_ERROR);
            return true;
        }
    } else {
        ESP_LOGW(TAG, "Bootstrap refresh failed; trying the saved websocket configuration");
    }

    // Keep an offline-friendly fallback for a transient bootstrap failure.
    // It succeeds only while the previously saved token is still valid.
    if (protocol_ && protocol_->OpenAudioChannel()) {
        ESP_LOGI(TAG, "Audio channel opened using saved websocket fallback");
        last_error_message_.clear();
        xEventGroupClearBits(event_group_, MAIN_EVENT_ERROR);
        return true;
    }
    ESP_LOGE(TAG, "Open audio channel failed after cached, bootstrap, and fallback attempts");
    return false;
}

void Application::ShowActivationCode(const std::string& code, const std::string& message) {
    struct digit_sound {
        char digit;
        const std::string_view& sound;
    };
    static const std::array<digit_sound, 10> digit_sounds{
        {digit_sound{'0', Lang::Sounds::OGG_0}, digit_sound{'1', Lang::Sounds::OGG_1},
         digit_sound{'2', Lang::Sounds::OGG_2}, digit_sound{'3', Lang::Sounds::OGG_3},
         digit_sound{'4', Lang::Sounds::OGG_4}, digit_sound{'5', Lang::Sounds::OGG_5},
         digit_sound{'6', Lang::Sounds::OGG_6}, digit_sound{'7', Lang::Sounds::OGG_7},
         digit_sound{'8', Lang::Sounds::OGG_8}, digit_sound{'9', Lang::Sounds::OGG_9}}};

    // This sentence uses 9KB of SRAM, so we need to wait for it to finish
    Alert(Lang::Strings::ACTIVATION, message.c_str(), "link", Lang::Sounds::OGG_ACTIVATION);
    auto display = Board::GetInstance().GetDisplay();
    display->ShowActivationCode(
        code.c_str(), ota_ == nullptr ? "" : ota_->GetActivationClaimUrl().c_str());

    for (const auto& digit : code) {
        auto it = std::find_if(digit_sounds.begin(), digit_sounds.end(),
                               [digit](const digit_sound& ds) { return ds.digit == digit; });
        if (it != digit_sounds.end()) {
            audio_service_.PlaySound(it->sound);
        }
    }
}

void Application::Alert(const char* status, const char* message, const char* emotion,
                        const std::string_view& sound) {
    ESP_LOGW(TAG, "Alert [%s] %s: %s", emotion, status, message);
    auto display = Board::GetInstance().GetDisplay();
    display->SetStatus(status);
    display->SetEmotion(emotion);
    display->SetChatMessage("system", message);
    if (!sound.empty()) {
        audio_service_.PlaySound(sound);
    }
}

void Application::DismissAlert() {
    if (GetDeviceState() == kDeviceStateIdle) {
        auto display = Board::GetInstance().GetDisplay();
        display->SetStatus(Lang::Strings::STANDBY);
#if !CONFIG_USE_EMOTE_MESSAGE_STYLE
        display->SetEmotion("neutral");
#endif
        display->SetChatMessage("system", "");
    }
}

void Application::ToggleChatState() { xEventGroupSetBits(event_group_, MAIN_EVENT_TOGGLE_CHAT); }

void Application::StartListening() { xEventGroupSetBits(event_group_, MAIN_EVENT_START_LISTENING); }

void Application::StopListening() { xEventGroupSetBits(event_group_, MAIN_EVENT_STOP_LISTENING); }

void Application::HandleToggleChatEvent() {
    auto state = GetDeviceState();

    if (state == kDeviceStateStarting || state == kDeviceStateUpgrading ||
        state == kDeviceStateFatalError) {
        return;
    }
    if (state == kDeviceStateActivating) {
        SetDeviceState(kDeviceStateIdle);
        return;
    } else if (state == kDeviceStateWifiConfiguring) {
        audio_service_.EnableAudioTesting(true);
        SetDeviceState(kDeviceStateAudioTesting);
        return;
    } else if (state == kDeviceStateAudioTesting) {
        audio_service_.EnableAudioTesting(false);
        SetDeviceState(kDeviceStateWifiConfiguring);
        return;
    }

    if (!protocol_) {
        ESP_LOGE(TAG, "Protocol not initialized");
        return;
    }

    const bool dialogue_active = state == kDeviceStateConnecting ||
        state == kDeviceStateListening || state == kDeviceStateSpeaking ||
        reply_pending_ || tts_playback_prepared_.load() ||
        !active_turn_id_.empty() || !active_tts_reply_id_.empty() ||
        reply_settle_active_;
    if (dialogue_active) {
        if (protocol_->IsAudioChannelOpened()) {
            protocol_->SendAbortSpeaking(kAbortReasonNone);
        }
        AbortDialogueToStandby("boot-stop", true);
        return;
    }

    if (state == kDeviceStateIdle) {
        ListeningMode mode = GetDefaultListeningMode();
        if (!protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update)
            Schedule([this, mode]() { ContinueOpenAudioChannel(mode); });
            return;
        }
        SetListeningMode(mode);
    }
}

void Application::ContinueOpenAudioChannel(ListeningMode mode) {
    // Check state again in case it was changed during scheduling
    if (GetDeviceState() != kDeviceStateConnecting) {
        ESP_LOGW(TAG, "Skip opening audio channel because state changed to %d",
                 static_cast<int>(GetDeviceState()));
        return;
    }
    ESP_LOGI(TAG, "ContinueOpenAudioChannel(mode=%d), channel_open=%d",
             static_cast<int>(mode),
             protocol_ ? protocol_->IsAudioChannelOpened() : false);

    // Switch to performance mode before connecting to reduce latency
    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);

    if (control_channel_task_handle_ != nullptr) {
        ESP_LOGI(TAG, "Waiting for idle control channel preconnect");
        return;
    }

    bool opened_channel = false;
    if (!protocol_->IsAudioChannelOpened()) {
        // Public/self-hosted bootstrap and the WSS handshake can take a few
        // seconds. Start the local encoder first so a user who speaks right
        // after pressing BOOT is buffered instead of losing the first phrase.
        // Packets are sent only after SetListeningMode() emits listen.start.
        audio_service_.EnableVoiceProcessing(true);
        if (!OpenAudioChannelWithConfigRefresh()) {
            // Return to idle so the device is not stuck in the connecting
            // state (not every failure path reports a network error)
            SetDeviceState(kDeviceStateIdle);
            return;
        }
        opened_channel = true;
    }

    if (opened_channel) {
        // A fresh channel must always emit listen.start, even if the local audio
        // processor was already running for wake-word detection.
        play_popup_on_listening_ = true;
    }
    SetListeningMode(mode);
}

void Application::HandleStartListeningEvent() {
    auto state = GetDeviceState();
    ESP_LOGI(TAG, "HandleStartListeningEvent(state=%d, protocol=%p, channel_open=%d)",
             static_cast<int>(state), protocol_.get(),
             protocol_ ? protocol_->IsAudioChannelOpened() : false);

    if (state == kDeviceStateActivating) {
        SetDeviceState(kDeviceStateIdle);
        return;
    } else if (state == kDeviceStateWifiConfiguring) {
        audio_service_.EnableAudioTesting(true);
        SetDeviceState(kDeviceStateAudioTesting);
        return;
    }

    if (!protocol_) {
        ESP_LOGE(TAG, "Protocol not initialized");
        return;
    }

    if (state == kDeviceStateIdle) {
        pending_connect_wake_word_.clear();
        if (control_channel_task_handle_ != nullptr ||
            !protocol_->IsAudioChannelOpened()) {
            SetDeviceState(kDeviceStateConnecting);
            // Schedule to let the state change be processed first (UI update)
            Schedule([this]() { ContinueOpenAudioChannel(kListeningModeManualStop); });
            return;
        }
        SetListeningMode(kListeningModeManualStop);
    } else if (state == kDeviceStateSpeaking) {
        AbortSpeaking(kAbortReasonNone);
        SetListeningMode(kListeningModeManualStop);
    }
}

void Application::HandleStopListeningEvent() {
    auto state = GetDeviceState();

    if (state == kDeviceStateAudioTesting) {
        audio_service_.EnableAudioTesting(false);
        SetDeviceState(kDeviceStateWifiConfiguring);
        return;
    } else if (state == kDeviceStateListening) {
        if (protocol_) {
            protocol_->SendStopListening();
        }
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::HandleWakeWordDetectedEvent() {
    if (!protocol_) {
        return;
    }

    auto state = GetDeviceState();
    auto wake_word = audio_service_.GetLastWakeWord();
    ESP_LOGI(TAG, "Wake word detected: %s (state: %d)", wake_word.c_str(), (int)state);

    if (state == kDeviceStateIdle) {
        Board::GetInstance().GetDisplay()->SetEmotion("surprised");
        BeginWakeWordInvoke(wake_word);
    } else if (state == kDeviceStateSpeaking || state == kDeviceStateListening) {
        AbortSpeaking(kAbortReasonWakeWordDetected);
        // Clear send queue to avoid sending residues to server
        while (audio_service_.PopPacketFromSendQueue())
            ;

        if (state == kDeviceStateListening) {
            protocol_->SendStartListening(GetDefaultListeningMode());
            audio_service_.ResetDecoder();
            audio_service_.PlaySound(Lang::Sounds::OGG_POPUP);
            // Re-enable wake word detection as it was stopped by the detection itself
            audio_service_.EnableWakeWordDetection(true);
        } else {
            // Play popup sound and start listening again
            play_popup_on_listening_ = true;
            SetListeningMode(GetDefaultListeningMode());
        }
    } else if (state == kDeviceStateActivating) {
        // Restart the activation check if the wake word is detected during activation
        SetDeviceState(kDeviceStateIdle);
    }
}

void Application::BeginWakeWordInvoke(const std::string& wake_word) {
    // Must run in the main task with the device in idle state
    reply_pending_ = false;
    pending_connect_wake_word_ = wake_word;
    audio_service_.ResetSendQueueOverflow();
    audio_service_.EncodeWakeWord();

    const bool control_channel_connecting = control_channel_task_handle_ != nullptr;
    if (control_channel_connecting || !protocol_->IsAudioChannelOpened()) {
        // Start local capture before publishing the connecting state.  The
        // state update schedules the WSS bootstrap onto the next main-loop
        // turn, so waiting until ContinueWakeWordInvoke() loses the opening
        // words of a single "wake word + question" utterance.
        audio_service_.EnableVoiceProcessing(true);
    }

    // Always pass through the connecting state, even if the audio channel is
    // already opened. ContinueWakeWordInvoke() rejects any other state, so
    // skipping this transition would silently drop the wake word invocation.
    if (!SetDeviceState(kDeviceStateConnecting)) {
        // BeginWakeWordInvoke() may have started the local encoder above. Do
        // not leave it running if the state machine rejects this invocation.
        audio_service_.EnableVoiceProcessing(false);
        // Wake word detection was stopped by the detection itself; restore it
        // so the device does not become unresponsive to wake words.
        audio_service_.EnableWakeWordDetection(true);
        pending_connect_wake_word_.clear();
        return;
    }

    if (control_channel_connecting || !protocol_->IsAudioChannelOpened()) {
        // Schedule to let the state change be processed first (UI update),
        // then continue with OpenAudioChannel which may block for ~1 second
        Schedule([this, wake_word]() { ContinueWakeWordInvoke(wake_word); });
        return;
    }
    // Channel already opened, continue directly
    ContinueWakeWordInvoke(wake_word);
}

void Application::ContinueWakeWordInvoke(const std::string& wake_word) {
    // Check state again in case it was changed during scheduling
    if (GetDeviceState() != kDeviceStateConnecting) {
        return;
    }

    // Switch to performance mode before connecting to reduce latency
    auto& board = Board::GetInstance();
    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);

    if (control_channel_task_handle_ != nullptr) {
        ESP_LOGI(TAG, "Waiting for idle control channel preconnect");
        return;
    }

    if (!protocol_->IsAudioChannelOpened()) {
        // Capture began in BeginWakeWordInvoke(). Keep its buffered frames
        // intact while Bootstrap/WSS connects; re-enabling here would reset
        // the front of a question spoken immediately after the wake word.
        if (!audio_service_.IsAudioProcessorRunning()) {
            audio_service_.EnableVoiceProcessing(true);
        }
        if (!OpenAudioChannelWithConfigRefresh()) {
            // Return to idle so the device is not stuck in the connecting
            // state (not every failure path reports a network error), and
            // wake word detection is re-enabled by the idle state handler.
            SetDeviceState(kDeviceStateIdle);
            pending_connect_wake_word_.clear();
            return;
        }
    }

    ESP_LOGI(TAG, "Wake word detected: %s", wake_word.c_str());
    pending_connect_wake_word_.clear();
#if CONFIG_SEND_WAKE_WORD_DATA
    // Encode and send the wake word data to the server
    while (auto packet = audio_service_.PopWakeWordPacket()) {
        protocol_->SendAudio(std::move(packet));
    }
    // Set the chat state to wake word detected
    protocol_->SendWakeWordDetected(wake_word);
    SetListeningMode(GetDefaultListeningMode());
#else
    // Set flag to play popup sound after state changes to listening
    // (PlaySound here would be cleared by ResetDecoder in EnableVoiceProcessing)
    play_popup_on_listening_ = true;
    SetListeningMode(GetDefaultListeningMode());
#endif
}

void Application::HandleStateChangedEvent() {
    DeviceState new_state = state_machine_.GetState();
    clock_ticks_ = 0;
    listening_capture_ready_ = false;
    vad_speech_edge_pending_.store(false, std::memory_order_release);
    // Any state change invalidates a pending deferred listening start;
    // the Listening case below re-arms it when needed.
    pending_listening_start_ = false;

    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();
    auto led = board.GetLed();
    led->OnStateChanged();

    switch (new_state) {
        case kDeviceStateUnknown:
        case kDeviceStateIdle:
            if (reply_settle_active_) {
                // FinishTtsPlayback deliberately passes through the idle
                // operational state during the bounded reply settle. Preserve
                // the selected finish face until the dialogue controller opens
                // the follow-up listening window instead of flashing sleep.
                audio_service_.EnableVoiceProcessing(false);
                audio_service_.EnableWakeWordDetection(false);
                break;
            }
            if (reply_pending_) {
                // VAD has ended a real utterance, but the cloud has not produced
                // speaker PCM yet. Preserve the current open-eyed listening face;
                // mapping this protocol idle state to standby flashes sleep eyes.
                audio_service_.EnableVoiceProcessing(false);
                audio_service_.EnableWakeWordDetection(false);
                break;
            }
            display->SetStatus(Lang::Strings::STANDBY);
            display->ClearChatMessages();    // Clear messages first
#if !CONFIG_USE_EMOTE_MESSAGE_STYLE
#if CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1
            display->SetEmotion("idle");
#else
            display->SetEmotion("neutral");  // Then set emotion (wechat mode checks child count)
#endif
#endif
            audio_service_.EnableVoiceProcessing(false);
            audio_service_.EnableWakeWordDetection(true);
            break;
        case kDeviceStateConnecting:
            display->SetStatus(Lang::Strings::CONNECTING);
#if !CONFIG_USE_EMOTE_MESSAGE_STYLE
            display->SetEmotion("neutral");
#endif
            display->SetChatMessage("system", "");
            break;
        case kDeviceStateListening:
            display->SetStatus(Lang::Strings::LISTENING);
#if !CONFIG_USE_EMOTE_MESSAGE_STYLE
            display->SetEmotion("neutral");
#endif

            if (post_playback_guard_active_) {
                // This simplex board has no playback reference for AEC. Keep
                // capture disabled briefly so speaker tail cannot start a
                // phantom user turn immediately after TTS drains.
                pending_listening_start_ = true;
                audio_service_.EnableVoiceProcessing(false);
                audio_service_.EnableWakeWordDetection(false);
                break;
            }

            // Make sure the audio processor is running
            if (play_popup_on_listening_ || !audio_service_.IsAudioProcessorRunning()) {
                // For auto mode, wait for the playback queue to drain before enabling
                // voice processing. This prevents audio truncation when STOP arrives
                // late due to network jitter. Instead of blocking the main loop here,
                // defer the start until MAIN_EVENT_PLAYBACK_DRAINED arrives.
                if (listening_mode_ == kListeningModeAutoStop && !audio_service_.IsPlaybackIdle()) {
                    pending_listening_start_ = true;
                } else {
                    StartListeningAudio();
                }
            } else {
                ConfigureWakeWordForListening();
            }
            break;
        case kDeviceStateSpeaking:
            display->SetStatus(Lang::Strings::SPEAKING);

            if (listening_mode_ != kListeningModeRealtime) {
                audio_service_.EnableVoiceProcessing(false);
#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1
                // Hensun is half-duplex. Listening for the wake word while its
                // own speaker is active can make the reply interrupt itself.
                audio_service_.EnableWakeWordDetection(false);
#else
                // Only AFE wake word can be detected in speaking mode
                audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
#endif
            }
            break;
        case kDeviceStateWifiConfiguring:
            audio_service_.EnableVoiceProcessing(false);
            audio_service_.EnableWakeWordDetection(false);
            break;
        default:
            // Do nothing
            break;
    }
}

void Application::StartListeningAudio() {
    // Runs in the main loop, either directly from HandleStateChangedEvent or
    // deferred via MAIN_EVENT_PLAYBACK_DRAINED once the playback queue drains.
    if (GetDeviceState() != kDeviceStateListening) {
        return;
    }

    if (audio_service_.ConsumeSendQueueOverflow()) {
        ESP_LOGE(TAG, "wake-buffer-overflow: discarding incomplete utterance");
        audio_service_.DiscardSendQueue();
        audio_service_.EnableVoiceProcessing(false);
        Board::GetInstance().GetDisplay()->ShowNotification(Lang::Strings::SERVER_TIMEOUT);
        AbortDialogueToStandby("wake-buffer-overflow", false);
        return;
    }

    // Register the new turn before releasing the pre-connect audio buffer.
    protocol_->SendStartListening(listening_mode_);
#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 || CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1
    protocol_->SendDeviceStage("capture_started", active_turn_id_);
#endif
    if (!audio_service_.IsAudioProcessorRunning()) {
        audio_service_.EnableVoiceProcessing(true);
    }
    // The follow-up deadline starts here, not when the UI first enters the
    // listening state. Playback drain and echo-guard time must not consume the
    // user's ten-second response window.
    clock_ticks_ = 0;
    listening_capture_ready_ = true;
    // A first utterance may already be queued while Bootstrap/WSS connected.
    // Re-arm the main loop so those packets are drained after listen.start.
    xEventGroupSetBits(event_group_, MAIN_EVENT_SEND_AUDIO);

    ConfigureWakeWordForListening();

    // Play popup sound after ResetDecoder (in EnableVoiceProcessing) has been called
    if (play_popup_on_listening_) {
        play_popup_on_listening_ = false;
        audio_service_.PlaySound(Lang::Sounds::OGG_POPUP);
    }
}

void Application::ConfigureWakeWordForListening() {
#ifdef CONFIG_WAKE_WORD_DETECTION_IN_LISTENING
    // Enable wake word detection in listening mode (configured via Kconfig)
    audio_service_.EnableWakeWordDetection(audio_service_.IsAfeWakeWord());
#else
    // Disable wake word detection in listening mode
    audio_service_.EnableWakeWordDetection(false);
#endif
}

void Application::CancelReplySettle() {
    reply_settle_active_ = false;
    post_playback_guard_active_ = false;
    if (post_playback_listen_timer_handle_ != nullptr) {
        esp_timer_stop(post_playback_listen_timer_handle_);
    }
}

void Application::StartTtsFirstPcmWatchdog() {
    CancelTtsFirstPcmWatchdog();
    if (tts_first_pcm_timer_handle_ == nullptr) {
        ESP_LOGE(TAG, "tts-first-pcm-watchdog-unavailable");
        AbortDialogueToStandby("tts-first-pcm-timeout", true);
        return;
    }
    const esp_err_t status = esp_timer_start_once(
        tts_first_pcm_timer_handle_, kTtsFirstPcmTimeoutUs);
    if (status != ESP_OK) {
        ESP_LOGE(TAG, "Unable to start first PCM watchdog: %s", esp_err_to_name(status));
        AbortDialogueToStandby("tts-first-pcm-timeout", true);
    }
}

void Application::CancelTtsFirstPcmWatchdog() {
    if (tts_first_pcm_timer_handle_ != nullptr) {
        esp_timer_stop(tts_first_pcm_timer_handle_);
    }
    if (event_group_ != nullptr) {
        xEventGroupClearBits(event_group_, MAIN_EVENT_TTS_FIRST_PCM_TIMEOUT);
    }
}

void Application::BeginReplySettle(bool resume_listening) {
    auto display = Board::GetInstance().GetDisplay();
    display->BeginReplySettle();
    reply_settle_active_ = true;
    post_playback_guard_active_ = resume_listening;

    esp_err_t timer_status = ESP_ERR_INVALID_STATE;
    if (post_playback_listen_timer_handle_ != nullptr) {
        esp_timer_stop(post_playback_listen_timer_handle_);
        timer_status = esp_timer_start_once(post_playback_listen_timer_handle_,
                                            kReplySettleDurationUs);
    }
    if (timer_status == ESP_OK) {
        return;
    }

    ESP_LOGW(TAG, "Unable to start reply settle timer: %s",
             esp_err_to_name(timer_status));
    // Preserve main-loop ordering even when the hardware timer is unavailable.
    // The caller still transitions to idle before this event is handled.
    xEventGroupSetBits(event_group_, MAIN_EVENT_POST_PLAYBACK_GUARD);
}

void Application::FinishTtsPlayback(std::string reply_id) {
    if (reply_id.empty() || reply_id != active_tts_reply_id_) {
        return;
    }
    CancelTtsFirstPcmWatchdog();
    const bool had_audio = tts_audio_started_;
    const std::string turn_id = active_turn_id_;
    if (!protocol_ || !protocol_->SendTtsState("drained", reply_id, turn_id)) {
        ESP_LOGE(TAG, "tts-drained-send-failed");
        AbortDialogueToStandby("tts-drained-send-failed", true);
        return;
    }
    reply_pending_ = false;
    pending_tts_stop_reply_id_.clear();
    active_tts_reply_id_.clear();
    tts_playback_prepared_.store(false);
    tts_audio_started_ = false;
    if (protocol_) {
#if CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 || CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1
        protocol_->SendDeviceStage("playback_drained", turn_id, reply_id);
#endif
    }
    ESP_LOGI(TAG, "TTS playback drained (decode drops=%lu)",
             (unsigned long)audio_service_.GetDecodeDropCount());
    active_turn_id_.clear();
#if CONFIG_HENSUN_ONE_SHOT_CONVERSATION
    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        protocol_->CloseAudioChannel();
    }
    if (had_audio) {
        BeginReplySettle(false);
    }
    SetDeviceState(kDeviceStateIdle);
#else
    if (listening_mode_ == kListeningModeManualStop) {
        if (had_audio) {
            BeginReplySettle(false);
        }
        SetDeviceState(kDeviceStateIdle);
    } else {
        if (had_audio) {
            BeginReplySettle(true);
            SetDeviceState(kDeviceStateIdle);
        } else {
            SetDeviceState(kDeviceStateListening);
        }
    }
#endif
}

void Application::AbortDialogueToStandby(const char* reason, bool close_audio_channel) {
    ESP_LOGW(TAG, "Aborting dialogue to standby: %s", reason ? reason : "unknown");
    CancelTtsFirstPcmWatchdog();
    reply_pending_ = false;
    CancelReplySettle();
    pending_listening_start_ = false;
    listening_capture_ready_ = false;
    vad_speech_detected_ = false;
    vad_speech_edge_pending_.store(false, std::memory_order_release);
    tts_playback_prepared_.store(false);
    tts_audio_started_ = false;
    active_tts_reply_id_.clear();
    pending_tts_stop_reply_id_.clear();
    active_turn_id_.clear();
    pending_connect_wake_word_.clear();
    clock_ticks_ = 0;
    audio_service_.EnableVoiceProcessing(false);
    audio_service_.DiscardSendQueue();
    audio_service_.ResetSendQueueOverflow();
    audio_service_.ResetDecoder();

    auto display = Board::GetInstance().GetDisplay();
    // TransitionTo is intentionally a no-op when the state is already idle.
    // Refresh the board-specific sleep face here so every abort path has the
    // same visible standby result even without a state-change callback.
    SetDeviceState(kDeviceStateIdle);
#if CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1
    display->SetEmotion("idle");
#endif
    display->SetStatus(Lang::Strings::STANDBY);
    display->ClearChatMessages();
    audio_service_.EnableWakeWordDetection(true);

    if (close_audio_channel && protocol_) {
        protocol_->CloseAudioChannel();
    }
}

void Application::Schedule(std::function<void()>&& callback) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        main_tasks_.push_back(std::move(callback));
    }
    xEventGroupSetBits(event_group_, MAIN_EVENT_SCHEDULE);
}

void Application::AbortSpeaking(AbortReason reason) {
    ESP_LOGI(TAG, "Abort speaking");
    aborted_ = true;
    if (protocol_) {
        protocol_->SendAbortSpeaking(reason);
    }
    AbortDialogueToStandby("abort-speaking", true);
}

void Application::SetListeningMode(ListeningMode mode) {
    listening_mode_ = mode;
    vad_speech_detected_ = false;
    reply_pending_ = false;
    SetDeviceState(kDeviceStateListening);
}

ListeningMode Application::GetDefaultListeningMode() const {
    return aec_mode_ == kAecOff ? kListeningModeAutoStop : kListeningModeRealtime;
}

void Application::Reboot() {
    ESP_LOGI(TAG, "Rebooting...");
    // Disconnect the audio channel
    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        protocol_->CloseAudioChannel();
    }
    protocol_.reset();
    audio_service_.Stop();

    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
}

bool Application::UpgradeFirmware(const std::string& url, const std::string& version) {
    auto& board = Board::GetInstance();
    auto display = board.GetDisplay();

    std::string upgrade_url = url;
    std::string version_info = version.empty() ? "(Manual upgrade)" : version;

    // Close audio channel if it's open
    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        ESP_LOGI(TAG, "Closing audio channel before firmware upgrade");
        protocol_->CloseAudioChannel();
    }
    ESP_LOGI(TAG, "Starting firmware upgrade from URL: %s", upgrade_url.c_str());

    Alert(Lang::Strings::OTA_UPGRADE, Lang::Strings::UPGRADING, "download",
          Lang::Sounds::OGG_UPGRADE);
    vTaskDelay(pdMS_TO_TICKS(3000));

    SetDeviceState(kDeviceStateUpgrading);

    std::string message = std::string(Lang::Strings::NEW_VERSION) + version_info;
    display->SetChatMessage("system", message.c_str());

    board.SetPowerSaveLevel(PowerSaveLevel::PERFORMANCE);
    audio_service_.Stop();
    vTaskDelay(pdMS_TO_TICKS(1000));

    bool upgrade_success = Ota::Upgrade(upgrade_url, [this, display](int progress, size_t speed) {
        char buffer[32];
        snprintf(buffer, sizeof(buffer), "%d%% %uKB/s", progress, speed / 1024);
        Schedule([display, message = std::string(buffer)]() {
            display->SetChatMessage("system", message.c_str());
        });
    });

    if (!upgrade_success) {
        // Upgrade failed, restart audio service and continue running
        ESP_LOGE(TAG,
                 "Firmware upgrade failed, restarting audio service and continuing operation...");
        audio_service_.Start();                              // Restart audio service
        board.SetPowerSaveLevel(PowerSaveLevel::LOW_POWER);  // Restore power save level
        Alert(Lang::Strings::ERROR, Lang::Strings::UPGRADE_FAILED, "cancel",
              Lang::Sounds::OGG_EXCLAMATION);
        vTaskDelay(pdMS_TO_TICKS(3000));
        return false;
    } else {
        // Upgrade success, reboot immediately
        ESP_LOGI(TAG, "Firmware upgrade successful, rebooting...");
        display->SetChatMessage("system", "Upgrade successful, rebooting...");
        vTaskDelay(pdMS_TO_TICKS(1000));  // Brief pause to show message
        Reboot();
        return true;
    }
}

void Application::WakeWordInvoke(const std::string& wake_word) {
    if (!protocol_) {
        return;
    }

    auto state = GetDeviceState();

    if (state == kDeviceStateIdle) {
        // May be called from outside the main task (e.g. board button
        // callbacks), so schedule the invocation instead of running it here
        Schedule([this, wake_word]() {
            if (GetDeviceState() == kDeviceStateIdle) {
                BeginWakeWordInvoke(wake_word);
            }
        });
    } else if (state == kDeviceStateSpeaking) {
        Schedule([this]() { AbortSpeaking(kAbortReasonNone); });
    } else if (state == kDeviceStateListening) {
        Schedule([this]() {
            if (protocol_) {
                protocol_->CloseAudioChannel();
            }
        });
    }
}

bool Application::CanEnterSleepMode() {
    if (GetDeviceState() != kDeviceStateIdle) {
        return false;
    }

    if (protocol_ && protocol_->IsAudioChannelOpened()) {
        return false;
    }

    if (!audio_service_.IsIdle()) {
        return false;
    }

    // Now it is safe to enter sleep mode
    return true;
}

void Application::RegisterMcpBroadcastCallback(std::function<void(const std::string&)> callback) {
    mcp_broadcast_callback_ = std::move(callback);
}

void Application::SendMcpMessage(const std::string& payload) {
    // Always schedule to run in main task for thread safety
    Schedule([this, payload]() {
        if (protocol_) {
            protocol_->SendMcpMessage(payload);
        }
        if (mcp_broadcast_callback_) {
            mcp_broadcast_callback_(payload);
        }
    });
}

void Application::SetAecMode(AecMode mode) {
    aec_mode_ = mode;
    Schedule([this]() {
        auto& board = Board::GetInstance();
        auto display = board.GetDisplay();
        switch (aec_mode_) {
            case kAecOff:
                audio_service_.EnableDeviceAec(false);
                display->ShowNotification(Lang::Strings::RTC_MODE_OFF);
                break;
            case kAecOnServerSide:
                audio_service_.EnableDeviceAec(false);
                display->ShowNotification(Lang::Strings::RTC_MODE_ON);
                break;
            case kAecOnDeviceSide:
                audio_service_.EnableDeviceAec(true);
                display->ShowNotification(Lang::Strings::RTC_MODE_ON);
                break;
        }

        // If the AEC mode is changed, close the audio channel
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
    });
}

void Application::PlaySound(const std::string_view& sound) { audio_service_.PlaySound(sound); }

void Application::ResetProtocol() {
    Schedule([this]() {
        // Close audio channel if opened
        if (protocol_ && protocol_->IsAudioChannelOpened()) {
            protocol_->CloseAudioChannel();
        }
        // Reset protocol
        protocol_.reset();
    });
}
