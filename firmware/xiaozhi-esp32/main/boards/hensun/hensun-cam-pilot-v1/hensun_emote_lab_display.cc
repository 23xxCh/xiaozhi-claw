#include "hensun_emote_lab_display.h"

#include "assets/lang_config.h"

#include <cstring>
#include <initializer_list>

#include <esp_err.h>
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>

namespace {

constexpr char kTag[] = "HensunEmoteLab";
constexpr char kPartitionLabel[] = "emote_gen";
constexpr int kFrameRate = 20;
constexpr int kRenderStripeHeight = 16;
constexpr int kPreviewDurationMs = 1500;
constexpr size_t kExpectedAnimationCount = 20;
constexpr const char* kExpectedAnimations[kExpectedAnimationCount] = {
    "sleep", "wake", "idle", "listening", "thinking", "speaking", "speaking_0",
    "speaking_1", "speaking_3", "happy", "caring", "curious", "surprised",
    "confused", "alert", "shy", "sad", "talk_base", "talk_happy", "talk_caring",
};
constexpr size_t kShowcaseAnimationCount = 14;
constexpr const char* kShowcaseAnimations[kShowcaseAnimationCount] = {
    "sleep", "wake", "idle", "listening", "thinking", "speaking", "happy", "caring",
    "curious", "surprised", "confused", "alert", "shy", "sad",
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
    if (!mouth_renderer_.Initialize(width, kRenderStripeHeight)) {
        ESP_LOGE(kTag, "speech mouth renderer init failed");
    }

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
    ESP_ERROR_CHECK(esp_lcd_panel_io_register_event_callbacks(panel_io_, &callbacks, this));

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

    // The display only needs the newest requested state. A one-item mailbox
    // prevents an old wake/listen/sleep request from running after speech has
    // already started.
    switch_queue_ = xQueueCreate(1, sizeof(SwitchRequest));
    if (switch_queue_ == nullptr) {
        ESP_LOGE(kTag, "xQueueCreate failed");
        return;
    }
    preview_flush_semaphore_ = xSemaphoreCreateBinary();
    if (preview_flush_semaphore_ == nullptr) {
        ESP_LOGE(kTag, "preview semaphore create failed");
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
    if (preview_flush_semaphore_ != nullptr) {
        vSemaphoreDelete(preview_flush_semaphore_);
        preview_flush_semaphore_ = nullptr;
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
        InvalidatePresentationState();
        presentation_state_.store(PresentationState::kListening);
        speaking_active_.store(false);
        awaiting_audio_.store(false);
        mouth_renderer_.SetActive(false);
        QueueAnimation("listening", false, true);
    } else if (std::strcmp(status, Lang::Strings::STANDBY) == 0) {
        // A reply has already reached the generic idle state at this point,
        // but its animation must finish before the display may sleep.
        if (reply_settle_pending_.load()) {
            return;
        }
        InvalidatePresentationState();
        presentation_state_.store(PresentationState::kSleep);
        speaking_active_.store(false);
        awaiting_audio_.store(false);
        mouth_renderer_.SetActive(false);
        QueueAnimation("sleep");
    } else if (std::strcmp(status, Lang::Strings::CONNECTING) == 0) {
        InvalidatePresentationState();
        presentation_state_.store(PresentationState::kThinking);
        speaking_active_.store(false);
        awaiting_audio_.store(false);
        mouth_renderer_.SetActive(false);
        QueueAnimation("thinking", false, true);
    } else if (std::strcmp(status, Lang::Strings::SPEAKING) == 0) {
        InvalidatePresentationState();
        presentation_state_.store(PresentationState::kAwaitingAudio);
        speech_level_.store(0);
        speaking_active_.store(false);
        awaiting_audio_.store(true);
        mouth_renderer_.SetActive(false);
        // tts.start only means the gateway has reserved a reply. Preserve the
        // current face until the first PCM block reaches I2S so a brief
        // awaiting-audio window does not flash the squinting thinking face.
    } else if (std::strcmp(status, Lang::Strings::ERROR) == 0) {
        InvalidatePresentationState();
        presentation_state_.store(PresentationState::kAlert);
        speaking_active_.store(false);
        awaiting_audio_.store(false);
        mouth_renderer_.SetActive(false);
        QueueAnimation("alert", true);
    }
}

void HensunEmoteLabDisplay::ShowNotification(const char* notification, int duration_ms) {
    (void)duration_ms;
    ESP_LOGI(kTag, "notification: %s", notification != nullptr ? notification : "");
}

void HensunEmoteLabDisplay::SetEmotion(const char* emotion) {
    bool urgent = false;
    const char* animation = MapEmotion(emotion, &urgent);
    if (std::strcmp(animation, "happy") == 0) {
        reply_emotion_.store(ReplyEmotion::kHappy);
    } else if (std::strcmp(animation, "caring") == 0) {
        reply_emotion_.store(ReplyEmotion::kCaring);
    } else if (std::strcmp(animation, "shy") == 0) {
        reply_emotion_.store(ReplyEmotion::kShy);
    } else if (std::strcmp(animation, "sad") == 0) {
        reply_emotion_.store(ReplyEmotion::kSad);
    } else {
        reply_emotion_.store(ReplyEmotion::kNeutral);
    }

    // LLM emotions describe the reply, not its playback lifecycle. Store them
    // for the 0.8 second reply settle instead of allowing a delayed event to
    // overwrite listening, thinking, or real speech.
    if (presentation_state_.load() == PresentationState::kAlert) {
        QueueAnimation(animation, urgent, true);
    }
}

void HensunEmoteLabDisplay::BeginReplySettle() {
    InvalidatePresentationState();
    presentation_state_.store(PresentationState::kReplySettle);
    speaking_active_.store(false);
    awaiting_audio_.store(false);
    mouth_renderer_.SetActive(false);
    reply_settle_pending_.store(true);

    const char* animation = "idle";
    switch (reply_emotion_.load()) {
        case ReplyEmotion::kHappy:
            animation = "happy";
            break;
        case ReplyEmotion::kCaring:
            animation = "caring";
            break;
        case ReplyEmotion::kShy:
            animation = "shy";
            break;
        case ReplyEmotion::kSad:
            animation = "sad";
            break;
        case ReplyEmotion::kNeutral:
            break;
    }
    QueueAnimation(animation, true, true);
}

void HensunEmoteLabDisplay::SetChatMessage(const char* role, const char* content) {
    (void)content;
    ESP_LOGD(kTag, "chat role: %s", role != nullptr ? role : "");
}

bool HensunEmoteLabDisplay::SetPreviewFrame(const uint16_t* pixels, size_t pixel_count,
                                            int width, int height, int stride_bytes) {
    if (player_ == nullptr || panel_ == nullptr || preview_flush_semaphore_ == nullptr ||
        pixels == nullptr || width <= 0 || height <= 0 ||
        stride_bytes < width * static_cast<int>(sizeof(uint16_t)) ||
        pixel_count < static_cast<size_t>(width) * height) {
        return false;
    }

    const size_t output_pixels = static_cast<size_t>(width_) * height_;
    auto* frame = static_cast<uint16_t*>(heap_caps_malloc(
        output_pixels * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (frame == nullptr) {
        ESP_LOGE(kTag, "camera preview allocation failed");
        return false;
    }

    const int source_stride_pixels = stride_bytes / static_cast<int>(sizeof(uint16_t));
    if (width == width_ && height == height_) {
        for (int row = 0; row < height_; ++row) {
            std::memcpy(frame + static_cast<size_t>(row) * width_,
                        pixels + static_cast<size_t>(row) * source_stride_pixels,
                        static_cast<size_t>(width_) * sizeof(uint16_t));
        }
    } else if (width == height_ && height == width_) {
        RotateRgb565Clockwise(pixels, width, height, source_stride_pixels, frame);
    } else {
        ESP_LOGW(kTag, "unsupported preview frame: %dx%d", width, height);
        heap_caps_free(frame);
        return false;
    }

    if (!Lock(1000)) {
        heap_caps_free(frame);
        return false;
    }
    for (int attempt = 0; attempt < 25 && animation_flushes_pending_.load() > 0; ++attempt) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    xSemaphoreTake(preview_flush_semaphore_, 0);
    preview_active_.store(true);
    preview_flush_pending_.store(true);
    const esp_err_t draw_result = esp_lcd_panel_draw_bitmap(
        panel_, 0, 0, width_, height_, frame);
    bool displayed = draw_result == ESP_OK;
    if (displayed) {
        displayed = xSemaphoreTake(preview_flush_semaphore_, pdMS_TO_TICKS(750)) == pdTRUE;
    }
    if (!displayed) {
        preview_flush_pending_.store(false);
        ESP_LOGE(kTag, "camera preview flush failed: %s", esp_err_to_name(draw_result));
    } else {
        vTaskDelay(pdMS_TO_TICKS(kPreviewDurationMs));
    }
    preview_active_.store(false);
    heap_caps_free(frame);
    Unlock();
    return displayed;
}

void HensunEmoteLabDisplay::UpdateStatusBar(bool update_all) {
    (void)update_all;
}

void HensunEmoteLabDisplay::SetPowerSaveMode(bool on) {
    if (panel_ == nullptr) {
        return;
    }
    // Soft standby must remain visibly alive so the user knows that the local
    // wake word is still available. Do not blank the panel here.
    esp_lcd_panel_disp_on_off(panel_, true);
    InvalidatePresentationState();
    speaking_active_.store(false);
    awaiting_audio_.store(false);
    mouth_renderer_.SetActive(false);
    presentation_state_.store(on ? PresentationState::kSleep : PresentationState::kIdle);
    QueueAnimation(on ? "sleep" : "idle", true, true);
}

void HensunEmoteLabDisplay::SetSpeechLevel(uint8_t level) {
    if (awaiting_audio_.load() && level == 0) {
        return;
    }
    if (awaiting_audio_.exchange(false)) {
        presentation_state_.store(PresentationState::kSpeaking);
        speaking_active_.store(true);
        mouth_renderer_.SetActive(true);
        QueueAnimation(ConversationAnimation(), false, true);
    }
    if (!speaking_active_.load()) {
        return;
    }
    const uint8_t current_level = speech_level_.load();
    const uint8_t next_level = QuantizeSpeechLevel(level, current_level);
    mouth_renderer_.SetLevel(next_level);
    if (next_level == current_level) {
        return;
    }
    speech_level_.store(next_level);
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
    if (active_display_ != nullptr && active_display_->panel_ != nullptr) {
        active_display_->animation_flushes_pending_.fetch_add(1);
        const void* frame = active_display_->mouth_renderer_.ComposeStripe(
            data, x_start, y_start, x_end, y_end);
        const esp_err_t result = esp_lcd_panel_draw_bitmap(
            active_display_->panel_, x_start, y_start, x_end, y_end, frame);
        if (result != ESP_OK) {
            active_display_->animation_flushes_pending_.fetch_sub(1);
            emote_gen_player_notify_flush_finished(manager);
        }
    }
}

bool HensunEmoteLabDisplay::IoReadyCallback(esp_lcd_panel_io_handle_t panel_io,
                                            esp_lcd_panel_io_event_data_t* event_data,
                                            void* user_ctx) {
    (void)panel_io;
    (void)event_data;
    auto* display = static_cast<HensunEmoteLabDisplay*>(user_ctx);
    if (display == nullptr) {
        return false;
    }
    if (display->preview_flush_pending_.exchange(false)) {
        BaseType_t task_woken = pdFALSE;
        xSemaphoreGiveFromISR(display->preview_flush_semaphore_, &task_woken);
        return task_woken == pdTRUE;
    }
    if (display->animation_flushes_pending_.load() > 0) {
        display->animation_flushes_pending_.fetch_sub(1);
        emote_gen_player_notify_flush_finished(display->player_);
    }
    return false;
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
        if (request.generation != animation_generation_.load()) {
            continue;
        }
        if (std::strcmp(current_animation_, request.animation) == 0) {
            continue;
        }
        const esp_err_t result = (request.urgent || request.immediate)
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
    for (const char* animation : kShowcaseAnimations) {
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

void HensunEmoteLabDisplay::InvalidatePresentationState() {
    reply_settle_pending_.store(false);
}

void HensunEmoteLabDisplay::CompleteReplySettle() {
    if (!reply_settle_pending_.exchange(false)) {
        return;
    }
    presentation_state_.store(PresentationState::kIdle);
    QueueAnimation("idle", true, true);
}

void HensunEmoteLabDisplay::QueueAnimation(const char* animation, bool urgent, bool immediate) {
    if (switch_queue_ == nullptr || animation == nullptr) {
        return;
    }
    SwitchRequest request = {};
    std::strncpy(request.animation, animation, sizeof(request.animation) - 1);
    request.urgent = urgent;
    request.immediate = immediate;
    request.generation = animation_generation_.fetch_add(1) + 1;
    const BaseType_t queued = xQueueOverwrite(switch_queue_, &request);
    if (queued != pdTRUE) {
        ESP_LOGW(kTag, "animation queue full: %s", animation);
    }
}

uint8_t HensunEmoteLabDisplay::QuantizeSpeechLevel(uint8_t level, uint8_t current_level) {
    switch (current_level) {
        case 0:
            return level >= 8 ? 1 : 0;
        case 1:
            if (level <= 3) return 0;
            return level >= 24 ? 2 : 1;
        case 2:
            if (level <= 14) return 1;
            return level >= 45 ? 3 : 2;
        case 3:
            if (level <= 34) return 2;
            return level >= 75 ? 4 : 3;
        default:
            return level <= 62 ? 3 : 4;
    }
}

const char* HensunEmoteLabDisplay::ConversationAnimation() const {
    switch (reply_emotion_.load()) {
        case ReplyEmotion::kHappy:
            return "talk_happy";
        case ReplyEmotion::kCaring:
            return "talk_caring";
        case ReplyEmotion::kShy:
            // The current shy asset has its own baked mouth. Use the shared
            // mouthless speech base while PCM overlay is active; the dedicated
            // shy animation is still shown during the reply settle.
            return "talk_base";
        case ReplyEmotion::kSad:
            // The current sad asset also has a baked mouth and would otherwise
            // render two mouths on top of each other during speech.
            return "talk_base";
        case ReplyEmotion::kNeutral:
        default:
            return "talk_base";
    }
}

void HensunEmoteLabDisplay::RotateRgb565Clockwise(
        const uint16_t* source, int source_width, int source_height,
        int source_stride_pixels, uint16_t* destination) {
    for (int source_y = 0; source_y < source_height; ++source_y) {
        for (int source_x = 0; source_x < source_width; ++source_x) {
            const int destination_x = source_height - 1 - source_y;
            const int destination_y = source_x;
            destination[static_cast<size_t>(destination_y) * source_height + destination_x] =
                source[static_cast<size_t>(source_y) * source_stride_pixels + source_x];
        }
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
    if (IsOneOf(emotion, {"sleep", "sleep_entered", "sleepy"})) {
        return "sleep";
    }
    if (IsOneOf(emotion, {"wake", "waking", "wake_word_detected"})) {
        return "wake";
    }
    if (IsOneOf(emotion, {"listening", "listening_started"})) {
        return "listening";
    }
    if (IsOneOf(emotion, {"thinking", "processing_started", "clarification",
                          "processing"})) {
        return "thinking";
    }
    if (IsOneOf(emotion, {"confused", "clarification_needed"})) {
        return "confused";
    }
    if (IsOneOf(emotion, {"curious", "question", "questioning"})) {
        return "curious";
    }
    if (IsOneOf(emotion, {"speaking", "query_result_ready"})) {
        return "speaking";
    }
    if (IsOneOf(emotion, {"surprised", "surprise"})) {
        return "surprised";
    }
    if (IsOneOf(emotion, {"happy", "positive_response", "laughter", "laughing", "amused"})) {
        return "happy";
    }
    if (IsOneOf(emotion, {"shy", "embarrassed", "affectionate"})) {
        return "shy";
    }
    if (IsOneOf(emotion, {"sad", "crying", "concerned", "apologetic", "worried"})) {
        return "sad";
    }
    if (IsOneOf(emotion, {"caring", "comfort_mode_entered", "fearful", "fear"})) {
        return "caring";
    }
    if (IsOneOf(emotion, {"safe_block", "content_safety_blocked", "network_unavailable",
                          "interrupted"})) {
        *urgent = true;
        return "alert";
    }
    ESP_LOGW(kTag, "unknown emotion: %s", emotion != nullptr ? emotion : "(null)");
    return "idle";
}
