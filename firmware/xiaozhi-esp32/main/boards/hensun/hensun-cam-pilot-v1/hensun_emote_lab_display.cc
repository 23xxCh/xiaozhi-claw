#include "hensun_emote_lab_display.h"

#include "assets/lang_config.h"
#include "emotion_mapper.h"
#include "hensun_panel.h"

#include <cstring>

#include <esp_log.h>
#include <esp_timer.h>

namespace {
constexpr char kTag[] = "HensunEmoteDisplay";
constexpr size_t kShowcaseAnimationCount = 6;
constexpr const char* kShowcaseAnimations[kShowcaseAnimationCount] = {
    "idle", "listening", "thinking", "speaking", "happy", "caring",
};
}  // namespace

HensunEmoteLabDisplay::HensunEmoteLabDisplay(
    HensunPanel& panel, int width, int height)
    : panel_(panel),
      renderer_(panel, width, height),
      preview_(panel, renderer_, width, height) {
    width_ = width;
    height_ = height;
}

HensunEmoteLabDisplay::~HensunEmoteLabDisplay() {
    showcase_active_.store(false);
}

void HensunEmoteLabDisplay::SetupUI() {
    setup_ui_called_ = true;
}

void HensunEmoteLabDisplay::SetStatus(const char* status) {
    if (status == nullptr) {
        return;
    }
    if (std::strcmp(status, Lang::Strings::LISTENING) == 0) {
        speech_envelope_.SetSpeaking(false);
        renderer_.Queue("listening");
    } else if (std::strcmp(status, Lang::Strings::STANDBY) == 0) {
        speech_envelope_.SetSpeaking(false);
        renderer_.Queue("idle");
    } else if (std::strcmp(status, Lang::Strings::SPEAKING) == 0) {
        speech_envelope_.SetSpeaking(true);
        renderer_.Queue("speaking");
    } else if (std::strcmp(status, Lang::Strings::ERROR) == 0) {
        speech_envelope_.SetSpeaking(false);
        renderer_.Queue("caring", true);
    }
}

void HensunEmoteLabDisplay::ShowNotification(
    const char* notification, int duration_ms) {
    (void)duration_ms;
    ESP_LOGI(kTag, "notification: %s", notification != nullptr ? notification : "");
}

void HensunEmoteLabDisplay::SetEmotion(const char* emotion) {
    const EmotionAnimation mapped = EmotionMapper::Map(emotion);
    speech_envelope_.SetSpeaking(std::strcmp(mapped.name, "speaking") == 0);
    renderer_.Queue(mapped.name, mapped.urgent);
}

void HensunEmoteLabDisplay::SetChatMessage(
    const char* role, const char* content) {
    (void)content;
    ESP_LOGD(kTag, "chat role: %s", role != nullptr ? role : "");
}

bool HensunEmoteLabDisplay::SetPreviewFrame(
    const uint16_t* pixels, size_t pixel_count, int width, int height,
    int stride_bytes) {
    return preview_.Show(pixels, pixel_count, width, height, stride_bytes);
}

void HensunEmoteLabDisplay::ConfigureSpeechEnvelope(
    uint32_t noise_floor, uint32_t reference_amplitude) {
    SpeechEnvelopeParameters parameters;
    parameters.noise_floor = noise_floor;
    parameters.reference_amplitude = reference_amplitude;
    speech_envelope_.SetParameters(parameters);
}

void HensunEmoteLabDisplay::UpdateStatusBar(bool update_all) {
    (void)update_all;
}

void HensunEmoteLabDisplay::SetPowerSaveMode(bool on) {
    panel_.SetPowerSaveMode(on);
}

void HensunEmoteLabDisplay::SetSpeechPcm(const std::vector<int16_t>& pcm) {
    const char* animation = speech_envelope_.UpdatePcm(pcm);
    if (animation != nullptr) {
        renderer_.Queue(animation, false, true);
    }
}

void HensunEmoteLabDisplay::SetSpeechLevel(uint8_t level) {
    const char* animation =
        speech_envelope_.UpdateLevel(level, esp_timer_get_time() / 1000);
    if (animation != nullptr) {
        renderer_.Queue(animation, false, true);
    }
}

void HensunEmoteLabDisplay::StartShowcase() {
    bool expected = false;
    if (!showcase_active_.compare_exchange_strong(expected, true)) {
        return;
    }
    if (xTaskCreate(ShowcaseTaskEntry, "hensun_emote_demo", 3072, this, 3,
                    nullptr) != pdPASS) {
        showcase_active_.store(false);
    }
}

bool HensunEmoteLabDisplay::Lock(int timeout_ms) {
    (void)timeout_ms;
    return renderer_.Lock();
}

void HensunEmoteLabDisplay::Unlock() {
    renderer_.Unlock();
}

void HensunEmoteLabDisplay::ShowcaseTaskEntry(void* context) {
    static_cast<HensunEmoteLabDisplay*>(context)->ShowcaseTask();
}

void HensunEmoteLabDisplay::ShowcaseTask() {
    for (const char* animation : kShowcaseAnimations) {
        if (!showcase_active_.load()) {
            break;
        }
        renderer_.Queue(animation, true);
        vTaskDelay(pdMS_TO_TICKS(2200));
    }
    showcase_active_.store(false);
    renderer_.Queue("idle");
    vTaskDelete(nullptr);
}
