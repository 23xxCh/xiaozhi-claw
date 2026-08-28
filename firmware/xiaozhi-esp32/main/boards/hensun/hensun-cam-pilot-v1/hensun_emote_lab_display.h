#pragma once

#include "display/display.h"
#include "emote_gen_player.h"
#include "hensun_speech_mouth_renderer.h"

#include <atomic>
#include <cstdint>

#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

class HensunEmoteLabDisplay final : public Display {
public:
    HensunEmoteLabDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel,
                          int width, int height);
    ~HensunEmoteLabDisplay() override;

    void SetupUI() override;
    void SetStatus(const char* status) override;
    void ShowNotification(const char* notification, int duration_ms = 3000) override;
    void SetEmotion(const char* emotion) override;
    void BeginReplySettle() override;
    void SetChatMessage(const char* role, const char* content) override;
    bool SetPreviewFrame(const uint16_t* pixels, size_t pixel_count,
                         int width, int height, int stride_bytes) override;
    void UpdateStatusBar(bool update_all = false) override;
    void SetPowerSaveMode(bool on) override;

    void SetSpeechLevel(uint8_t level);
    void StartShowcase();

private:
    enum class PresentationState : uint8_t {
        kSleep,
        kIdle,
        kListening,
        kThinking,
        kAwaitingAudio,
        kSpeaking,
        kReplySettle,
        kAlert,
    };

    enum class ReplyEmotion : uint8_t {
        kNeutral,
        kHappy,
        kCaring,
        kShy,
        kSad,
    };

    struct SwitchRequest {
        char animation[16];
        bool urgent;
        bool immediate;
        uint32_t generation;
    };

    static void FlushCallback(int x_start, int y_start, int x_end, int y_end,
                              const void* data, emote_gen_player_handle_t manager);
    static bool IoReadyCallback(esp_lcd_panel_io_handle_t panel_io,
                                esp_lcd_panel_io_event_data_t* event_data, void* user_ctx);
    static void SwitchTaskEntry(void* context);
    static void ShowcaseTaskEntry(void* context);
    static void ReplySettleTimerCallback(void* context);
    static void IdleSleepTimerCallback(void* context);

    bool Lock(int timeout_ms = 0) override;
    void Unlock() override;
    void SwitchTask();
    void ShowcaseTask();
    uint32_t InvalidatePresentationTimers();
    void CompleteReplySettle();
    void EnterSleepAfterIdle();
    void QueueAnimation(const char* animation, bool urgent = false, bool immediate = false);
    bool ValidatePack() const;
    const char* MapEmotion(const char* emotion, bool* urgent) const;
    const char* ConversationAnimation() const;
    static uint8_t QuantizeSpeechLevel(uint8_t level, uint8_t current_level);
    static void RotateRgb565Clockwise(const uint16_t* source, int source_width,
                                      int source_height, int source_stride_pixels,
                                      uint16_t* destination);

    static HensunEmoteLabDisplay* active_display_;

    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    emote_gen_player_handle_t player_ = nullptr;
    QueueHandle_t switch_queue_ = nullptr;
    SemaphoreHandle_t preview_flush_semaphore_ = nullptr;
    TaskHandle_t switch_task_ = nullptr;
    esp_timer_handle_t reply_settle_timer_handle_ = nullptr;
    esp_timer_handle_t idle_sleep_timer_handle_ = nullptr;
    std::atomic<bool> showcase_active_{false};
    std::atomic<bool> speaking_active_{false};
    std::atomic<bool> awaiting_audio_{false};
    std::atomic<bool> reply_settle_pending_{false};
    std::atomic<bool> idle_sleep_pending_{false};
    std::atomic<bool> preview_active_{false};
    std::atomic<bool> preview_flush_pending_{false};
    std::atomic<uint32_t> animation_flushes_pending_{0};
    std::atomic<uint32_t> animation_generation_{0};
    std::atomic<uint8_t> speech_level_{0};
    std::atomic<PresentationState> presentation_state_{PresentationState::kSleep};
    std::atomic<ReplyEmotion> reply_emotion_{ReplyEmotion::kNeutral};
    std::atomic<uint32_t> presentation_generation_{0};
    std::atomic<uint32_t> reply_settle_generation_{0};
    std::atomic<uint32_t> idle_sleep_generation_{0};
    HensunSpeechMouthRenderer mouth_renderer_;
    char current_animation_[16] = {};
};
