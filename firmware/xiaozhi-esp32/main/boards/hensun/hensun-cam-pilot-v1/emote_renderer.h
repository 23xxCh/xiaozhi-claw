#pragma once

#include "emote_gen_player.h"

#include <cstddef>
#include <cstdint>

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

class HensunPanel;

class EmoteRenderer final {
public:
    EmoteRenderer(HensunPanel& panel, int width, int height);
    ~EmoteRenderer();

    EmoteRenderer(const EmoteRenderer&) = delete;
    EmoteRenderer& operator=(const EmoteRenderer&) = delete;

    bool ready() const { return player_ != nullptr && switch_queue_ != nullptr; }
    void Queue(const char* animation, bool urgent = false, bool immediate = false);
    bool Lock();
    void Unlock();

private:
    struct SwitchRequest {
        char animation[16];
        bool urgent;
        bool immediate;
    };

    static void SwitchTaskEntry(void* context);
    void SwitchTask();
    bool ValidatePack() const;

    HensunPanel& panel_;
    emote_gen_player_handle_t player_ = nullptr;
    QueueHandle_t switch_queue_ = nullptr;
    TaskHandle_t switch_task_ = nullptr;
    char current_animation_[16] = {};
};
