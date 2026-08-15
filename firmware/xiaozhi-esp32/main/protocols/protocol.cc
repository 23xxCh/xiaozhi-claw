#include "protocol.h"
#include "assets.h"

#include <esp_log.h>

#define TAG "Protocol"

void Protocol::AddTextFontCapabilities(cJSON* root) {
    auto capability = Assets::GetInstance().text_font_capability();
    cJSON* features = cJSON_GetObjectItem(root, "features");
    if (cJSON_IsObject(features)) {
        cJSON_AddBoolToObject(features, "glyph_push", capability.glyph_push);
    }

    if (!capability.glyph_push) {
        return;
    }
    cJSON* font = cJSON_CreateObject();
    cJSON_AddStringToObject(font, "bundle", capability.bundle.c_str());
    cJSON_AddStringToObject(font, "charset", capability.charset.c_str());
    cJSON_AddNumberToObject(font, "size", capability.size);
    cJSON_AddNumberToObject(font, "bpp", capability.bpp);
    cJSON_AddItemToObject(root, "text_font", font);
}

void Protocol::OnIncomingJson(std::function<void(const cJSON* root)> callback) {
    on_incoming_json_ = callback;
}

void Protocol::OnIncomingAudio(
    std::function<void(std::unique_ptr<AudioStreamPacket> packet)> callback) {
    on_incoming_audio_ = callback;
}

void Protocol::OnAudioChannelOpened(std::function<void()> callback) {
    on_audio_channel_opened_ = callback;
}

void Protocol::OnAudioChannelClosed(std::function<void()> callback) {
    on_audio_channel_closed_ = callback;
}

void Protocol::OnNetworkError(std::function<void(const std::string& message)> callback) {
    on_network_error_ = callback;
}

void Protocol::OnConnected(std::function<void()> callback) { on_connected_ = callback; }

void Protocol::OnDisconnected(std::function<void()> callback) { on_disconnected_ = callback; }

void Protocol::SetError(const std::string& message) {
    error_occurred_ = true;
    if (on_network_error_ != nullptr) {
        on_network_error_(message);
    }
}

void Protocol::SendAbortSpeaking(AbortReason reason) {
    std::string message = "{\"session_id\":\"" + session_id_ + "\",\"type\":\"abort\"";
    if (reason == kAbortReasonWakeWordDetected) {
        message += ",\"reason\":\"wake_word_detected\"";
    }
    message += "}";
    SendText(message);
}

void Protocol::SendWakeWordDetected(const std::string& wake_word) {
    std::string json = "{\"session_id\":\"" + session_id_ +
                       "\",\"type\":\"listen\",\"state\":\"detect\",\"text\":\"" + wake_word +
                       "\"}";
    SendText(json);
}

void Protocol::SendStartListening(ListeningMode mode) {
    std::string message = "{\"session_id\":\"" + session_id_ + "\"";
    message += ",\"type\":\"listen\",\"state\":\"start\"";
    if (mode == kListeningModeRealtime) {
        message += ",\"mode\":\"realtime\"";
    } else if (mode == kListeningModeAutoStop) {
        message += ",\"mode\":\"auto\"";
    } else {
        message += ",\"mode\":\"manual\"";
    }
    message += "}";
    SendText(message);
}

void Protocol::SendStopListening() {
    std::string message =
        "{\"session_id\":\"" + session_id_ + "\",\"type\":\"listen\",\"state\":\"stop\"}";
    SendText(message);
}

void Protocol::SendTtsState(const std::string& state, const std::string& reply_id) {
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "session_id", session_id_.c_str());
    cJSON_AddStringToObject(root, "type", "tts");
    cJSON_AddStringToObject(root, "state", state.c_str());
    cJSON_AddStringToObject(root, "reply_id", reply_id.c_str());
    char* json = cJSON_PrintUnformatted(root);
    if (json != nullptr) {
        SendText(json);
        cJSON_free(json);
    }
    cJSON_Delete(root);
}

void Protocol::SendMcpMessage(const std::string& payload) {
    std::string message =
        "{\"session_id\":\"" + session_id_ + "\",\"type\":\"mcp\",\"payload\":" + payload + "}";
    SendText(message);
}

void Protocol::SendDeviceConfigAck(const std::string& command_id, int config_version,
                                   int schema_version, bool applied,
                                   const HensunDeviceConfigValues* config,
                                   const std::string& error_code) {
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "session_id", session_id_.c_str());
    cJSON_AddStringToObject(root, "type", "device_config_ack");
    cJSON_AddStringToObject(root, "command_id", command_id.c_str());
    cJSON_AddNumberToObject(root, "config_version", config_version);
    cJSON_AddNumberToObject(root, "schema_version", schema_version);
    cJSON_AddStringToObject(root, "status", applied ? "applied" : "failed");
    if (applied && config != nullptr) {
        cJSON* applied_values = cJSON_AddObjectToObject(root, "applied_values");
        cJSON_AddNumberToObject(applied_values, HENSUN_CONFIG_KEY_AUDIO_SPEAKER_VOLUME,
                                config->audio_speaker_volume);
        cJSON_AddNumberToObject(applied_values, HENSUN_CONFIG_KEY_DISPLAY_BRIGHTNESS,
                                config->display_brightness);
        cJSON_AddNumberToObject(applied_values, HENSUN_CONFIG_KEY_AUDIO_WAKE_THRESHOLD,
                                config->audio_wake_threshold);
        cJSON_AddStringToObject(applied_values, HENSUN_CONFIG_KEY_AUDIO_VAD_MODE,
                                config->audio_vad_mode.c_str());
        cJSON_AddNumberToObject(applied_values, HENSUN_CONFIG_KEY_AUDIO_VAD_MIN_NOISE_MS,
                                config->audio_vad_min_noise_ms);
        cJSON_AddNumberToObject(applied_values,
                                HENSUN_CONFIG_KEY_DISPLAY_LIP_SYNC_NOISE_FLOOR,
                                config->display_lip_sync_noise_floor);
        cJSON_AddNumberToObject(applied_values,
                                HENSUN_CONFIG_KEY_DISPLAY_LIP_SYNC_REFERENCE_AMPLITUDE,
                                config->display_lip_sync_reference_amplitude);
        cJSON* legacy_values = cJSON_AddObjectToObject(root, "applied");
        cJSON_AddNumberToObject(legacy_values, "speaker_volume",
                                config->audio_speaker_volume);
        cJSON_AddNumberToObject(legacy_values, "screen_brightness",
                                config->display_brightness);
    } else {
        cJSON_AddStringToObject(root, "error_code", error_code.c_str());
    }
    char* json = cJSON_PrintUnformatted(root);
    if (json != nullptr) {
        SendText(json);
        cJSON_free(json);
    }
    cJSON_Delete(root);
}

bool Protocol::IsTimeout() const {
    const int kTimeoutSeconds = 120;
    auto now = std::chrono::steady_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::seconds>(now - last_incoming_time_);
    bool timeout = duration.count() > kTimeoutSeconds;
    if (timeout) {
        ESP_LOGE(TAG, "Channel timeout %ld seconds", (long)duration.count());
    }
    return timeout;
}
