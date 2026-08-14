#pragma once

#include "display/display.h"
#include "emote_gen_player.h"

#include <atomic>
#include <cstdint>

#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
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
    void SetChatMessage(const char* role, const char* content) override;
    void UpdateStatusBar(bool update_all = false) override;
    void SetPowerSaveMode(bool on) override;

    void SetSpeechLevel(uint8_t level);
    void StartShowcase();

private:
    struct SwitchRequest {
        char animation[16];
        bool urgent;
    };

    static void FlushCallback(int x_start, int y_start, int x_end, int y_end,
                              const void* data, emote_gen_player_handle_t manager);
    static bool IoReadyCallback(esp_lcd_panel_io_handle_t panel_io,
                                esp_lcd_panel_io_event_data_t* event_data, void* user_ctx);
    static void SwitchTaskEntry(void* context);
    static void ShowcaseTaskEntry(void* context);

    bool Lock(int timeout_ms = 0) override;
    void Unlock() override;
    void SwitchTask();
    void ShowcaseTask();
    void QueueAnimation(const char* animation, bool urgent = false);
    bool ValidatePack() const;
    const char* MapEmotion(const char* emotion, bool* urgent) const;

    static HensunEmoteLabDisplay* active_display_;

    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    emote_gen_player_handle_t player_ = nullptr;
    QueueHandle_t switch_queue_ = nullptr;
    TaskHandle_t switch_task_ = nullptr;
    std::atomic<bool> showcase_active_{false};
    char current_animation_[16] = {};
};
