#pragma once

#include "camera_preview.h"
#include "display/display.h"
#include "emote_renderer.h"
#include "speech_envelope.h"

#include <atomic>
#include <cstdint>
#include <vector>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

class HensunPanel;

class HensunEmoteLabDisplay final : public Display {
public:
    HensunEmoteLabDisplay(HensunPanel& panel, int width, int height);
    ~HensunEmoteLabDisplay() override;

    void SetupUI() override;
    void SetStatus(const char* status) override;
    void ShowNotification(const char* notification, int duration_ms = 3000) override;
    void SetEmotion(const char* emotion) override;
    void SetChatMessage(const char* role, const char* content) override;
    bool SetPreviewFrame(const uint16_t* pixels, size_t pixel_count,
                         int width, int height, int stride_bytes) override;
    void ConfigureSpeechEnvelope(uint32_t noise_floor,
                                 uint32_t reference_amplitude) override;
    void UpdateStatusBar(bool update_all = false) override;
    void SetPowerSaveMode(bool on) override;

    void SetSpeechPcm(const std::vector<int16_t>& pcm);
    void SetSpeechLevel(uint8_t level);
    void StartShowcase();

private:
    static void ShowcaseTaskEntry(void* context);
    bool Lock(int timeout_ms = 0) override;
    void Unlock() override;
    void ShowcaseTask();

    HensunPanel& panel_;
    EmoteRenderer renderer_;
    SpeechEnvelope speech_envelope_;
    CameraPreview preview_;
    std::atomic<bool> showcase_active_{false};
};
