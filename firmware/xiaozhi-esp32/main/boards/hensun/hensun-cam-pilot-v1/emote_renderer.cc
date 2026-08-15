#include "emote_renderer.h"

#include "hensun_panel.h"

#include <cstring>

#include <esp_err.h>
#include <esp_log.h>

namespace {
constexpr char kTag[] = "EmoteRenderer";
constexpr char kPartitionLabel[] = "emote_gen";
constexpr int kFrameRate = 20;
constexpr size_t kExpectedAnimationCount = 10;
constexpr const char* kExpectedAnimations[kExpectedAnimationCount] = {
    "idle", "listening", "thinking", "speaking", "speaking_0",
    "speaking_1", "speaking_3", "happy", "caring", "sleep",
};
}  // namespace

EmoteRenderer::EmoteRenderer(HensunPanel& panel, int width, int height)
    : panel_(panel) {
    panel_.EnableEmoteFlush();
    const emote_gen_player_config_t config = {
        .flags = {
            .swap = true,
            .double_buffer = true,
            .buff_dma = true,
            .buff_spiram = false,
        },
        .gfx_emote = {
            .h_res = width,
            .v_res = height,
            .fps = kFrameRate,
        },
        .buffers = {
            .buf_pixels = static_cast<size_t>(width * 16),
        },
        .task = {
            .task_priority = 5,
            .task_stack = 6 * 1024,
            .task_affinity = 0,
            .task_stack_in_ext = false,
        },
        .flush_cb = HensunPanel::FlushAnimation,
        .update_cb = nullptr,
    };
    player_ = emote_gen_player_init(&config);
    if (player_ == nullptr) {
        ESP_LOGE(kTag, "player init failed");
        return;
    }
    panel_.AttachPlayer(player_);

    const emote_gen_player_data_t assets = {
        .type = EMOTE_GEN_PLAYER_SOURCE_PARTITION,
        .source = {
            .partition_label = kPartitionLabel,
        },
        .flags = {
            .mmap_enable = 1,
            .preload_to_spiram = 1,
        },
    };
    if (emote_gen_player_mount_assets(player_, &assets) != ESP_OK ||
        !ValidatePack()) {
        ESP_LOGE(kTag, "invalid Hensun emote pack");
        return;
    }
    emote_gen_player_set_tip_text(player_, "");
    ESP_LOGI(kTag, "Hensun emote assets preloaded before audio engine start");

    switch_queue_ = xQueueCreate(6, sizeof(SwitchRequest));
    if (switch_queue_ == nullptr) {
        ESP_LOGE(kTag, "switch queue create failed");
        return;
    }
    if (xTaskCreate(SwitchTaskEntry, "hensun_emote_switch", 4096, this, 4,
                    &switch_task_) != pdPASS) {
        ESP_LOGE(kTag, "switch task create failed");
        switch_task_ = nullptr;
        return;
    }
    Queue("idle", true);
}

EmoteRenderer::~EmoteRenderer() {
    if (switch_task_ != nullptr) {
        vTaskDelete(switch_task_);
        switch_task_ = nullptr;
    }
    if (switch_queue_ != nullptr) {
        vQueueDelete(switch_queue_);
        switch_queue_ = nullptr;
    }
    panel_.AttachPlayer(nullptr);
    if (player_ != nullptr) {
        emote_gen_player_deinit(player_);
        player_ = nullptr;
    }
}

void EmoteRenderer::Queue(const char* animation, bool urgent, bool immediate) {
    if (switch_queue_ == nullptr || animation == nullptr) {
        return;
    }
    SwitchRequest request = {};
    std::strncpy(request.animation, animation, sizeof(request.animation) - 1);
    request.urgent = urgent;
    request.immediate = immediate;
    if (urgent) {
        xQueueReset(switch_queue_);
    }
    const BaseType_t queued = immediate
                                  ? xQueueSendToFront(switch_queue_, &request, 0)
                                  : xQueueSend(switch_queue_, &request, 0);
    if (queued != pdTRUE) {
        ESP_LOGW(kTag, "animation queue full: %s", animation);
    }
}

bool EmoteRenderer::Lock() {
    if (player_ == nullptr) {
        return false;
    }
    gfx_handle_t gfx = emote_gen_player_get_gfx_handle(player_);
    return gfx != nullptr && gfx_emote_lock(gfx) == ESP_OK;
}

void EmoteRenderer::Unlock() {
    if (player_ == nullptr) {
        return;
    }
    gfx_handle_t gfx = emote_gen_player_get_gfx_handle(player_);
    if (gfx != nullptr) {
        gfx_emote_unlock(gfx);
    }
}

void EmoteRenderer::SwitchTaskEntry(void* context) {
    static_cast<EmoteRenderer*>(context)->SwitchTask();
}

void EmoteRenderer::SwitchTask() {
    SwitchRequest request = {};
    while (xQueueReceive(switch_queue_, &request, portMAX_DELAY) == pdTRUE) {
        if (std::strcmp(current_animation_, request.animation) == 0) {
            continue;
        }
        const esp_err_t result =
            (request.urgent || request.immediate)
                ? emote_gen_player_anim_now_name(player_, request.animation, true)
                : emote_gen_player_anim_fade_name(player_, request.animation, true);
        if (result == ESP_OK) {
            std::strncpy(current_animation_, request.animation,
                         sizeof(current_animation_) - 1);
            current_animation_[sizeof(current_animation_) - 1] = '\0';
            emote_gen_player_set_tip_text(player_, "");
        } else {
            ESP_LOGE(kTag, "animation switch failed: %s (%s)", request.animation,
                     esp_err_to_name(result));
        }
    }
    vTaskDelete(nullptr);
}

bool EmoteRenderer::ValidatePack() const {
    if (emote_gen_player_get_index_count(player_) != kExpectedAnimationCount) {
        return false;
    }
    for (const char* expected : kExpectedAnimations) {
        bool found = false;
        for (size_t index = 0; index < kExpectedAnimationCount; ++index) {
            const auto* entry = emote_gen_player_get_index_entry(player_, index);
            if (entry != nullptr && std::strcmp(entry->name, expected) == 0) {
                found = true;
                break;
            }
        }
        if (!found) {
            ESP_LOGE(kTag, "missing animation: %s", expected);
            return false;
        }
    }
    return true;
}
