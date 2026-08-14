#include "hensun_emote_lab_display.h"

#include "assets/lang_config.h"

#include <cstring>
#include <initializer_list>

#include <esp_err.h>
#include <esp_log.h>

namespace {

constexpr char kTag[] = "HensunEmoteLab";
constexpr char kPartitionLabel[] = "emote_gen";
constexpr int kFrameRate = 20;
constexpr size_t kExpectedAnimationCount = 6;
constexpr const char* kExpectedAnimations[kExpectedAnimationCount] = {
    "idle", "listening", "thinking", "speaking", "happy", "caring",
};

bool IsOneOf(const char* value, std::initializer_list<const char*> choices) {
    if (value == nullptr) {
        return false;
    }
    for (const char* choice : choices) {
        if (std::strcmp(value, choice) == 0) {
            return true;
        }
    }
    return false;
}

}  // namespace

HensunEmoteLabDisplay* HensunEmoteLabDisplay::active_display_ = nullptr;

HensunEmoteLabDisplay::HensunEmoteLabDisplay(esp_lcd_panel_io_handle_t panel_io,
                                             esp_lcd_panel_handle_t panel,
                                             int width, int height)
    : panel_io_(panel_io), panel_(panel) {
    width_ = width;
    height_ = height;
    active_display_ = this;

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
        .flush_cb = FlushCallback,
        .update_cb = nullptr,
    };
    player_ = emote_gen_player_init(&config);
    if (player_ == nullptr) {
        ESP_LOGE(kTag, "player init failed");
        return;
    }

    const esp_lcd_panel_io_callbacks_t callbacks = {
        .on_color_trans_done = IoReadyCallback,
    };
    ESP_ERROR_CHECK(esp_lcd_panel_io_register_event_callbacks(panel_io_, &callbacks, player_));

    const emote_gen_player_data_t assets = {
        .type = EMOTE_GEN_PLAYER_SOURCE_PARTITION,
        .source = {
            .partition_label = kPartitionLabel,
        },
        .flags = {
            .mmap_enable = 1,
        },
    };
    if (emote_gen_player_mount_assets(player_, &assets) != ESP_OK || !ValidatePack()) {
        ESP_LOGE(kTag, "invalid Hensun emote pack");
        return;
    }
    emote_gen_player_set_tip_text(player_, "");

    switch_queue_ = xQueueCreate(6, sizeof(SwitchRequest));
    if (switch_queue_ == nullptr) {
        ESP_LOGE(kTag, "xQueueCreate failed");
        return;
    }
    if (xTaskCreate(SwitchTaskEntry, "hensun_emote_switch", 4096, this, 4, &switch_task_) != pdPASS) {
        ESP_LOGE(kTag, "switch task create failed");
        switch_task_ = nullptr;
        return;
    }
    QueueAnimation("idle", true);
}

HensunEmoteLabDisplay::~HensunEmoteLabDisplay() {
    showcase_active_.store(false);
    if (switch_task_ != nullptr) {
        vTaskDelete(switch_task_);
        switch_task_ = nullptr;
    }
    if (switch_queue_ != nullptr) {
        vQueueDelete(switch_queue_);
        switch_queue_ = nullptr;
    }
    if (player_ != nullptr) {
        emote_gen_player_deinit(player_);
        player_ = nullptr;
    }
    if (active_display_ == this) {
        active_display_ = nullptr;
    }
}

void HensunEmoteLabDisplay::SetupUI() {
    setup_ui_called_ = true;
}

void HensunEmoteLabDisplay::SetStatus(const char* status) {
    if (status == nullptr) {
        return;
    }
    if (std::strcmp(status, Lang::Strings::LISTENING) == 0) {
        QueueAnimation("listening");
    } else if (std::strcmp(status, Lang::Strings::STANDBY) == 0) {
        QueueAnimation("idle");
    } else if (std::strcmp(status, Lang::Strings::SPEAKING) == 0) {
        QueueAnimation("speaking");
    } else if (std::strcmp(status, Lang::Strings::ERROR) == 0) {
        QueueAnimation("caring", true);
    }
}

void HensunEmoteLabDisplay::ShowNotification(const char* notification, int duration_ms) {
    (void)duration_ms;
    ESP_LOGI(kTag, "notification: %s", notification != nullptr ? notification : "");
}

void HensunEmoteLabDisplay::SetEmotion(const char* emotion) {
    bool urgent = false;
    const char* animation = MapEmotion(emotion, &urgent);
    QueueAnimation(animation, urgent);
}

void HensunEmoteLabDisplay::SetChatMessage(const char* role, const char* content) {
    (void)content;
    ESP_LOGD(kTag, "chat role: %s", role != nullptr ? role : "");
}

void HensunEmoteLabDisplay::UpdateStatusBar(bool update_all) {
    (void)update_all;
}

void HensunEmoteLabDisplay::SetPowerSaveMode(bool on) {
    esp_lcd_panel_disp_on_off(panel_, !on);
}

void HensunEmoteLabDisplay::SetSpeechLevel(uint8_t level) {
    (void)level;
}

void HensunEmoteLabDisplay::StartShowcase() {
    bool expected = false;
    if (!showcase_active_.compare_exchange_strong(expected, true)) {
        return;
    }
    if (xTaskCreate(ShowcaseTaskEntry, "hensun_emote_demo", 3072, this, 3, nullptr) != pdPASS) {
        showcase_active_.store(false);
    }
}

void HensunEmoteLabDisplay::FlushCallback(int x_start, int y_start, int x_end, int y_end,
                                          const void* data, emote_gen_player_handle_t manager) {
    (void)manager;
    if (active_display_ != nullptr && active_display_->panel_ != nullptr) {
        esp_lcd_panel_draw_bitmap(active_display_->panel_, x_start, y_start, x_end, y_end, data);
    }
}

bool HensunEmoteLabDisplay::IoReadyCallback(esp_lcd_panel_io_handle_t panel_io,
                                            esp_lcd_panel_io_event_data_t* event_data,
                                            void* user_ctx) {
    (void)panel_io;
    (void)event_data;
    if (user_ctx != nullptr) {
        emote_gen_player_notify_flush_finished(
            static_cast<emote_gen_player_handle_t>(user_ctx));
    }
    return true;
}

void HensunEmoteLabDisplay::SwitchTaskEntry(void* context) {
    static_cast<HensunEmoteLabDisplay*>(context)->SwitchTask();
}

void HensunEmoteLabDisplay::ShowcaseTaskEntry(void* context) {
    static_cast<HensunEmoteLabDisplay*>(context)->ShowcaseTask();
}

bool HensunEmoteLabDisplay::Lock(int timeout_ms) {
    (void)timeout_ms;
    gfx_handle_t gfx = emote_gen_player_get_gfx_handle(player_);
    return gfx != nullptr && gfx_emote_lock(gfx) == ESP_OK;
}

void HensunEmoteLabDisplay::Unlock() {
    gfx_handle_t gfx = emote_gen_player_get_gfx_handle(player_);
    if (gfx != nullptr) {
        gfx_emote_unlock(gfx);
    }
}

void HensunEmoteLabDisplay::SwitchTask() {
    SwitchRequest request = {};
    while (xQueueReceive(switch_queue_, &request, portMAX_DELAY) == pdTRUE) {
        if (std::strcmp(current_animation_, request.animation) == 0) {
            continue;
        }
        const esp_err_t result = request.urgent
            ? emote_gen_player_anim_now_name(player_, request.animation, true)
            : emote_gen_player_anim_fade_name(player_, request.animation, true);
        if (result == ESP_OK) {
            std::strncpy(current_animation_, request.animation, sizeof(current_animation_) - 1);
            current_animation_[sizeof(current_animation_) - 1] = '\0';
            emote_gen_player_set_tip_text(player_, "");
        } else {
            ESP_LOGE(kTag, "animation switch failed: %s (%s)", request.animation,
                     esp_err_to_name(result));
        }
    }
    vTaskDelete(nullptr);
}

void HensunEmoteLabDisplay::ShowcaseTask() {
    for (const char* animation : kExpectedAnimations) {
        if (!showcase_active_.load()) {
            break;
        }
        QueueAnimation(animation, true);
        vTaskDelay(pdMS_TO_TICKS(2200));
    }
    showcase_active_.store(false);
    QueueAnimation("idle");
    vTaskDelete(nullptr);
}

void HensunEmoteLabDisplay::QueueAnimation(const char* animation, bool urgent) {
    if (switch_queue_ == nullptr || animation == nullptr) {
        return;
    }
    SwitchRequest request = {};
    std::strncpy(request.animation, animation, sizeof(request.animation) - 1);
    request.urgent = urgent;
    if (urgent) {
        xQueueReset(switch_queue_);
    }
    if (xQueueSend(switch_queue_, &request, 0) != pdTRUE) {
        ESP_LOGW(kTag, "animation queue full: %s", animation);
    }
}

bool HensunEmoteLabDisplay::ValidatePack() const {
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

const char* HensunEmoteLabDisplay::MapEmotion(const char* emotion, bool* urgent) const {
    *urgent = false;
    if (IsOneOf(emotion, {"neutral", "idle", "idle_entered"})) {
        return "idle";
    }
    if (IsOneOf(emotion, {"listening", "listening_started", "wake_word_detected"})) {
        return "listening";
    }
    if (IsOneOf(emotion, {"thinking", "processing_started", "clarification",
                          "clarification_needed"})) {
        return "thinking";
    }
    if (IsOneOf(emotion, {"speaking", "query_result_ready"})) {
        return "speaking";
    }
    if (IsOneOf(emotion, {"happy", "positive_response", "laughter", "laughing",
                          "amused", "surprised"})) {
        return "happy";
    }
    if (IsOneOf(emotion, {"caring", "comfort_mode_entered", "sad", "fearful", "fear"})) {
        return "caring";
    }
    if (IsOneOf(emotion, {"safe_block", "content_safety_blocked", "network_unavailable",
                          "interrupted"})) {
        *urgent = true;
        return "caring";
    }
    ESP_LOGW(kTag, "unknown emotion: %s", emotion != nullptr ? emotion : "(null)");
    return "idle";
}
