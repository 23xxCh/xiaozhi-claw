#pragma once

#include "emote_gen_player.h"

#include <atomic>
#include <cstdint>

#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

class HensunPanel final {
public:
    HensunPanel(int width, int height);
    ~HensunPanel();

    HensunPanel(const HensunPanel&) = delete;
    HensunPanel& operator=(const HensunPanel&) = delete;

    esp_lcd_panel_io_handle_t io() const { return panel_io_; }
    esp_lcd_panel_handle_t panel() const { return panel_; }
    int width() const { return width_; }
    int height() const { return height_; }

    void EnableEmoteFlush();
    void AttachPlayer(emote_gen_player_handle_t player);
    bool WaitForAnimationFlushes(int timeout_ms);
    bool DrawPreview(const uint16_t* pixels, int duration_ms);
    void SetPowerSaveMode(bool on);

    static void FlushAnimation(int x_start, int y_start, int x_end, int y_end,
                               const void* data, emote_gen_player_handle_t manager);

private:
    static void AnimationFlushWatchdogEntry(void* context);
    void AnimationFlushWatchdog();
    static bool IoReadyCallback(esp_lcd_panel_io_handle_t panel_io,
                                 esp_lcd_panel_io_event_data_t* event_data,
                                 void* user_ctx);

    static HensunPanel* active_panel_;

    int width_ = 0;
    int height_ = 0;
    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    std::atomic<emote_gen_player_handle_t> player_{nullptr};
    SemaphoreHandle_t preview_flush_semaphore_ = nullptr;
    TaskHandle_t animation_flush_watchdog_task_ = nullptr;
    std::atomic<bool> preview_flush_pending_{false};
    std::atomic<uint32_t> animation_flushes_pending_{0};
    std::atomic<int64_t> animation_flush_started_us_{0};
};
