#include "hensun_face_display.h"

#include "application.h"
#include "assets/lang_config.h"

#include <esp_log.h>
#include <esp_timer.h>

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <utility>

namespace {

constexpr char kTag[] = "HensunFace";
constexpr uint32_t kAnimationPeriodMs = 50;
constexpr uint32_t kShowcaseStateFrames = 24;  // 1.2 seconds at 20 FPS.

constexpr uint32_t kBackgroundColor = 0x020811;
constexpr uint32_t kCyanColor = 0x49F6FF;
constexpr uint32_t kWhiteColor = 0xF4FFFF;
constexpr uint32_t kPinkColor = 0xFF6FB7;
constexpr uint32_t kAmberColor = 0xFFC247;
constexpr uint32_t kRedColor = 0xFF4D67;

struct FaceRoute {
    const char* input;
    HensunFaceState state;
    uint32_t hold_ms;
};

// Canonical names are sent by the Hensun cloud in llm.emotion or alert.emotion.
// Compatibility aliases cover emotion names already emitted by XiaoZhi firmware/services.
constexpr FaceRoute kFaceRoutes[] = {
    {"ready", HensunFaceState::kReady, 3000},
    {"idle", HensunFaceState::kIdle, 3000},
    {"listening", HensunFaceState::kListening, 3000},
    {"thinking", HensunFaceState::kThinking, 5000},
    {"speaking", HensunFaceState::kSpeaking, 3000},
    {"interrupted", HensunFaceState::kInterrupted, 2500},
    {"happy", HensunFaceState::kHappy, 5000},
    {"curious", HensunFaceState::kCurious, 5000},
    {"caring", HensunFaceState::kCaring, 5000},
    {"reminder", HensunFaceState::kReminder, 6000},
    {"alarm", HensunFaceState::kReminder, 6000},
    {"timer_done", HensunFaceState::kReminder, 6000},
    {"reminder_due", HensunFaceState::kReminder, 6000},
    {"network_error", HensunFaceState::kNetworkError, 5000},
    {"cloud_off", HensunFaceState::kNetworkError, 5000},
    {"cancel", HensunFaceState::kNetworkError, 5000},
    {"sleep", HensunFaceState::kSleep, 8000},
    {"laughing", HensunFaceState::kLaughing, 5000},
    {"funny", HensunFaceState::kFunny, 5000},
    {"loving", HensunFaceState::kLoving, 5000},
    {"embarrassed", HensunFaceState::kEmbarrassed, 5000},
    {"confident", HensunFaceState::kConfident, 5000},
    {"delicious", HensunFaceState::kDelicious, 5000},
    {"sad", HensunFaceState::kSad, 5000},
    {"crying", HensunFaceState::kCrying, 5000},
    {"sleepy", HensunFaceState::kSleepy, 5000},
    {"silly", HensunFaceState::kSilly, 5000},
    {"angry", HensunFaceState::kAngry, 5000},
    {"surprised", HensunFaceState::kSurprised, 5000},
    {"shocked", HensunFaceState::kShocked, 5000},
    {"winking", HensunFaceState::kWinking, 5000},
    {"relaxed", HensunFaceState::kRelaxed, 5000},
    {"confused", HensunFaceState::kConfused, 5000},
    {"proud", HensunFaceState::kProud, 5000},
    {"excited", HensunFaceState::kExcited, 5000},
    {"worried", HensunFaceState::kWorried, 5000},
    {"warning", HensunFaceState::kWorried, 5000},
    {"apology", HensunFaceState::kApology, 5000},
    {"pairing", HensunFaceState::kPairing, 5000},
    {"link", HensunFaceState::kPairing, 5000},
    {"network_ok", HensunFaceState::kNetworkOk, 3000},
    {"robot_2", HensunFaceState::kNetworkOk, 3000},
    {"updating", HensunFaceState::kUpdating, 8000},
    {"download", HensunFaceState::kUpdating, 8000},
    {"cloud_download", HensunFaceState::kUpdating, 8000},
    {"safe_block", HensunFaceState::kSafeBlock, 6000},
    {"content_blocked", HensunFaceState::kSafeBlock, 6000},
    {"safety_block", HensunFaceState::kSafeBlock, 6000},
};

const FaceRoute* FindFaceRoute(const char* input) {
    if (input == nullptr) {
        return nullptr;
    }
    for (const auto& route : kFaceRoutes) {
        if (std::strcmp(input, route.input) == 0) {
            return &route;
        }
    }
    return nullptr;
}

bool StartsWith(const char* text, const char* prefix) {
    if (text == nullptr || prefix == nullptr) {
        return false;
    }
    return std::strncmp(text, prefix, std::strlen(prefix)) == 0;
}

void StyleShape(lv_obj_t* object, uint32_t color, lv_opa_t opacity = LV_OPA_COVER) {
    lv_obj_remove_style_all(object);
    lv_obj_set_style_bg_color(object, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(object, opacity, 0);
    lv_obj_set_style_border_width(object, 0, 0);
    lv_obj_set_style_pad_all(object, 0, 0);
    lv_obj_set_style_radius(object, LV_RADIUS_CIRCLE, 0);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_SCROLLABLE);
}

void Place(lv_obj_t* object, int width, int height, int x, int y) {
    lv_obj_set_size(object, width, height);
    lv_obj_align(object, LV_ALIGN_CENTER, x, y);
}

}  // namespace

HensunFaceDisplay::HensunFaceDisplay(esp_lcd_panel_io_handle_t panel_io,
                                     esp_lcd_panel_handle_t panel, int width, int height,
                                     int offset_x, int offset_y, bool mirror_x, bool mirror_y,
                                     bool swap_xy)
    : SpiLcdDisplay(panel_io, panel, width, height, offset_x, offset_y, mirror_x, mirror_y,
                    swap_xy) {
}

HensunFaceDisplay::~HensunFaceDisplay() {
    if (animation_timer_ != nullptr) {
        DisplayLockGuard lock(this);
        lv_timer_delete(animation_timer_);
        animation_timer_ = nullptr;
    }
}

void HensunFaceDisplay::SetupUI() {
    LcdDisplay::SetupUI();

    DisplayLockGuard lock(this);
    if (face_layer_ != nullptr || container_ == nullptr) {
        return;
    }

    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(kBackgroundColor), 0);
    lv_obj_set_style_bg_color(container_, lv_color_hex(kBackgroundColor), 0);
    lv_obj_set_style_bg_opa(container_, LV_OPA_COVER, 0);
    if (top_bar_ != nullptr) {
        lv_obj_set_style_bg_color(top_bar_, lv_color_hex(kBackgroundColor), 0);
        lv_obj_set_style_bg_opa(top_bar_, LV_OPA_70, 0);
    }
    if (bottom_bar_ != nullptr) {
        lv_obj_set_style_bg_color(bottom_bar_, lv_color_hex(kBackgroundColor), 0);
    }
    if (status_label_ != nullptr) {
        lv_obj_set_style_text_color(status_label_, lv_color_hex(kCyanColor), 0);
    }
    if (network_label_ != nullptr) {
        lv_obj_set_style_text_color(network_label_, lv_color_hex(kCyanColor), 0);
    }
    if (mute_label_ != nullptr) {
        lv_obj_set_style_text_color(mute_label_, lv_color_hex(kCyanColor), 0);
    }
    if (battery_label_ != nullptr) {
        lv_obj_add_flag(battery_label_, LV_OBJ_FLAG_HIDDEN);
    }
    if (emoji_box_ != nullptr) {
        lv_obj_add_flag(emoji_box_, LV_OBJ_FLAG_HIDDEN);
    }

    CreateFaceObjects();
    SetFaceStateLocked(StateFromDevice());
    animation_timer_ = lv_timer_create(AnimationTimerCallback, kAnimationPeriodMs, this);
    ESP_LOGI(kTag, "Original 36-state face set ready at 20 FPS");
}

void HensunFaceDisplay::CreateFaceObjects() {
    face_layer_ = lv_obj_create(container_);
    lv_obj_remove_style_all(face_layer_);
    lv_obj_set_size(face_layer_, 220, 220);
    lv_obj_align(face_layer_, LV_ALIGN_CENTER, 0, 2);
    lv_obj_set_style_bg_opa(face_layer_, LV_OPA_TRANSP, 0);
    lv_obj_set_style_pad_all(face_layer_, 0, 0);
    lv_obj_set_style_border_width(face_layer_, 0, 0);
    lv_obj_remove_flag(face_layer_, LV_OBJ_FLAG_SCROLLABLE);

    left_eye_glow_ = lv_obj_create(face_layer_);
    right_eye_glow_ = lv_obj_create(face_layer_);
    left_eye_ = lv_obj_create(face_layer_);
    right_eye_ = lv_obj_create(face_layer_);
    left_highlight_ = lv_obj_create(face_layer_);
    right_highlight_ = lv_obj_create(face_layer_);
    left_brow_ = lv_obj_create(face_layer_);
    right_brow_ = lv_obj_create(face_layer_);
    mouth_ = lv_obj_create(face_layer_);
    left_cheek_ = lv_obj_create(face_layer_);
    right_cheek_ = lv_obj_create(face_layer_);

    StyleShape(left_eye_glow_, kCyanColor, LV_OPA_30);
    StyleShape(right_eye_glow_, kCyanColor, LV_OPA_30);
    StyleShape(left_eye_, kCyanColor);
    StyleShape(right_eye_, kCyanColor);
    StyleShape(left_highlight_, kWhiteColor);
    StyleShape(right_highlight_, kWhiteColor);
    StyleShape(left_brow_, kCyanColor);
    StyleShape(right_brow_, kCyanColor);
    StyleShape(mouth_, kCyanColor);
    StyleShape(left_cheek_, kPinkColor, LV_OPA_80);
    StyleShape(right_cheek_, kPinkColor, LV_OPA_80);

    accent_label_ = lv_label_create(face_layer_);
    lv_obj_set_width(accent_label_, 80);
    lv_obj_set_style_text_align(accent_label_, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(accent_label_, lv_color_hex(kCyanColor), 0);
    lv_label_set_text(accent_label_, "");
    lv_obj_align(accent_label_, LV_ALIGN_CENTER, 0, -88);
}

void HensunFaceDisplay::SetStatus(const char* status) {
    LvglDisplay::SetStatus(status);
    if (!setup_ui_called_) {
        return;
    }

    DisplayLockGuard lock(this);
    last_status_ = status != nullptr ? status : "";
    if (showcase_active_ || transient_active_) {
        return;
    }
    auto state = StateFromDevice();
    ESP_LOGI(kTag, "Face route source=device input=%d state=%s",
             static_cast<int>(Application::GetInstance().GetDeviceState()), StateName(state));
    SetFaceStateLocked(state);
}

void HensunFaceDisplay::ShowNotification(const char* notification, int duration_ms) {
    LvglDisplay::ShowNotification(notification, duration_ms);
    if (!setup_ui_called_ || face_layer_ == nullptr) {
        return;
    }
    if (notification == nullptr) {
        return;
    }

    DisplayLockGuard lock(this);
    if (showcase_active_) {
        return;
    }
    if (StartsWith(notification, Lang::Strings::CONNECTED_TO) ||
        std::strcmp(notification, Lang::Strings::CONNECTION_SUCCESSFUL) == 0) {
        ShowTransientStateLocked(HensunFaceState::kNetworkOk, 3000, "notification",
                                 "network_connected");
    } else if (StartsWith(notification, Lang::Strings::CONNECT_TO) ||
               std::strcmp(notification, Lang::Strings::SCANNING_WIFI) == 0 ||
               std::strcmp(notification, Lang::Strings::ENTERING_WIFI_CONFIG_MODE) == 0) {
        ShowTransientStateLocked(HensunFaceState::kPairing, 3000, "notification",
                                 "network_pairing");
    }
}

void HensunFaceDisplay::SetEmotion(const char* emotion) {
    if (!setup_ui_called_) {
        return;
    }

    DisplayLockGuard lock(this);
    if (showcase_active_) {
        return;
    }
    if (emotion == nullptr || std::strcmp(emotion, "neutral") == 0) {
        transient_active_ = false;
        transient_frames_remaining_ = 0;
        auto state = StateFromDevice();
        ESP_LOGI(kTag, "Face route source=emotion input=neutral state=%s", StateName(state));
        SetFaceStateLocked(state);
        return;
    }

    const auto* route = FindFaceRoute(emotion);
    if (route == nullptr) {
        ESP_LOGW(kTag, "Unknown face route source=emotion input=%s; using device state", emotion);
        transient_active_ = false;
        transient_frames_remaining_ = 0;
        SetFaceStateLocked(StateFromDevice());
        return;
    }
    ShowTransientStateLocked(route->state, route->hold_ms, "emotion", emotion);
}

void HensunFaceDisplay::SetPreviewImage(std::unique_ptr<LvglImage> image) {
    const bool has_preview = image != nullptr;
    LcdDisplay::SetPreviewImage(std::move(image));

    if (!setup_ui_called_ || face_layer_ == nullptr) {
        return;
    }

    DisplayLockGuard lock(this);
    preview_active_ = has_preview;
    if (preview_active_) {
        lv_obj_add_flag(face_layer_, LV_OBJ_FLAG_HIDDEN);
    } else {
        if (emoji_box_ != nullptr) {
            lv_obj_add_flag(emoji_box_, LV_OBJ_FLAG_HIDDEN);
        }
        lv_obj_remove_flag(face_layer_, LV_OBJ_FLAG_HIDDEN);
    }
}

void HensunFaceDisplay::StartShowcase() {
    if (!setup_ui_called_ || face_layer_ == nullptr) {
        ESP_LOGW(kTag, "Showcase requested before display setup");
        return;
    }

    DisplayLockGuard lock(this);
    showcase_active_ = true;
    transient_active_ = false;
    transient_frames_remaining_ = 0;
    showcase_frame_ = 0;
    SetFaceStateLocked(HensunFaceState::kReady);
    if (status_label_ != nullptr) {
        lv_label_set_text(status_label_, "01 READY");
    }
    ESP_LOGI(kTag, "Starting 36-state display showcase");
}

void HensunFaceDisplay::AnimationTimerCallback(lv_timer_t* timer) {
    auto* display = static_cast<HensunFaceDisplay*>(lv_timer_get_user_data(timer));
    display->TickAnimation();
}

void HensunFaceDisplay::TickAnimation() {
    if (face_layer_ == nullptr || preview_active_) {
        return;
    }

    if (showcase_active_) {
        ++showcase_frame_;
        if (showcase_frame_ >= kShowcaseStateFrames) {
            showcase_frame_ = 0;
            auto next = static_cast<uint8_t>(state_) + 1;
            if (next >= static_cast<uint8_t>(HensunFaceState::kCount)) {
                showcase_active_ = false;
                SetFaceStateLocked(StateFromDevice());
                if (status_label_ != nullptr) {
                    lv_label_set_text(status_label_,
                                      last_status_.empty() ? StateName(state_) : last_status_.c_str());
                }
                ESP_LOGI(kTag, "36-state display showcase complete");
            } else {
                SetFaceStateLocked(static_cast<HensunFaceState>(next));
                if (status_label_ != nullptr) {
                    char label[24];
                    std::snprintf(label, sizeof(label), "%02u %s",
                                  static_cast<unsigned>(next + 1), StateName(state_));
                    lv_label_set_text(status_label_, label);
                }
            }
        }
    } else if (transient_active_) {
        if (transient_frames_remaining_ > 0) {
            --transient_frames_remaining_;
        }
        if (transient_frames_remaining_ == 0) {
            transient_active_ = false;
            auto state = StateFromDevice();
            ESP_LOGI(kTag, "Face route source=timeout state=%s", StateName(state));
            SetFaceStateLocked(state);
        }
    }

    ++animation_frame_;
    RenderFace();
}

void HensunFaceDisplay::SetFaceStateLocked(HensunFaceState state) {
    if (state_ == state && animation_frame_ != 0) {
        return;
    }
    state_ = state;
    animation_frame_ = 0;
    RenderFace();
}

void HensunFaceDisplay::ShowTransientStateLocked(HensunFaceState state, uint32_t duration_ms,
                                                 const char* source, const char* input) {
    transient_active_ = true;
    transient_frames_remaining_ = std::max<uint32_t>(1, duration_ms / kAnimationPeriodMs);
    ESP_LOGI(kTag, "Face route source=%s input=%s state=%s hold_ms=%lu", source,
             input != nullptr ? input : "", StateName(state),
             static_cast<unsigned long>(duration_ms));
    SetFaceStateLocked(state);
}

HensunFaceState HensunFaceDisplay::StateFromDevice() const {
    switch (Application::GetInstance().GetDeviceState()) {
        case kDeviceStateStarting:
            return HensunFaceState::kReady;
        case kDeviceStateWifiConfiguring:
        case kDeviceStateActivating:
        case kDeviceStateConnecting:
            return HensunFaceState::kPairing;
        case kDeviceStateIdle:
            return HensunFaceState::kIdle;
        case kDeviceStateListening:
        case kDeviceStateAudioTesting:
            return HensunFaceState::kListening;
        case kDeviceStateSpeaking:
            return HensunFaceState::kSpeaking;
        case kDeviceStateUpgrading:
            return HensunFaceState::kUpdating;
        case kDeviceStateFatalError:
            return HensunFaceState::kNetworkError;
        default:
            return HensunFaceState::kReady;
    }
}

const char* HensunFaceDisplay::StateName(HensunFaceState state) {
    static constexpr const char* kNames[] = {
        "READY",      "IDLE",    "LISTENING", "THINKING", "SPEAKING", "INTERRUPTED",
        "HAPPY",      "CURIOUS", "CARING",    "REMINDER", "NET ERROR", "SLEEP",
        "LAUGHING",   "FUNNY",   "LOVING",    "SHY",      "CONFIDENT", "DELICIOUS",
        "SAD",        "CRYING",  "SLEEPY",    "SILLY",    "ANGRY",     "SURPRISED",
        "SHOCKED",    "WINKING", "RELAXED",   "CONFUSED", "PROUD",     "EXCITED",
        "WORRIED",    "APOLOGY", "PAIRING",   "NET OK",   "UPDATING",  "SAFE BLOCK",
    };
    static_assert(sizeof(kNames) / sizeof(kNames[0]) ==
                  static_cast<uint8_t>(HensunFaceState::kCount));
    auto index = static_cast<uint8_t>(state);
    return index < static_cast<uint8_t>(HensunFaceState::kCount) ? kNames[index] : "UNKNOWN";
}

void HensunFaceDisplay::RenderFace() {
    if (face_layer_ == nullptr) {
        return;
    }

    const int64_t started_us = esp_timer_get_time();
    const int pulse = static_cast<int>(animation_frame_ % 20);
    const bool blink = state_ == HensunFaceState::kIdle && animation_frame_ % 100 >= 94;

    int eye_width = 50;
    int eye_height = blink ? 7 : 68;
    int eye_x = 48;
    int eye_y = -18;
    int brow_y = -65;
    int mouth_width = 34;
    int mouth_height = 7;
    int mouth_y = 62;
    int pupil_shift = 0;
    int left_eye_height = 0;
    int right_eye_height = 0;
    int mouth_x = 0;
    uint32_t main_color = kCyanColor;
    uint32_t accent_color = kCyanColor;
    const char* accent = "";
    bool show_highlights = !blink;
    bool show_cheeks = false;
    bool open_mouth = false;

    switch (state_) {
        case HensunFaceState::kReady:
            eye_height = 62;
            accent = "*";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kIdle:
            mouth_width = 24;
            break;
        case HensunFaceState::kListening:
            eye_width = 52 + std::min(pulse, 20 - pulse) / 2;
            eye_height = 70 + std::min(pulse, 20 - pulse) / 2;
            accent = "))";
            break;
        case HensunFaceState::kThinking:
            pupil_shift = static_cast<int>((animation_frame_ / 8) % 5) - 2;
            mouth_width = 12;
            accent = "...";
            break;
        case HensunFaceState::kSpeaking:
            eye_height = 58;
            mouth_width = 30 + (pulse % 5) * 3;
            mouth_height = 12 + (pulse % 4) * 5;
            open_mouth = true;
            break;
        case HensunFaceState::kInterrupted:
            eye_height = 42;
            brow_y = -56;
            mouth_width = 30;
            accent = "!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kHappy:
            eye_height = 10;
            mouth_width = 48;
            mouth_height = 9;
            show_highlights = false;
            show_cheeks = true;
            accent = "*";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kCurious:
            eye_height = 72;
            pupil_shift = 5;
            mouth_width = 13;
            mouth_height = 13;
            accent = "?";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kCaring:
            eye_height = 56;
            eye_y = -14;
            mouth_width = 40;
            show_cheeks = true;
            accent = "*";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kReminder:
            eye_height = 64;
            mouth_width = 18;
            mouth_height = 16;
            open_mouth = true;
            accent = "!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kNetworkError:
            eye_height = 28;
            mouth_width = 38;
            main_color = animation_frame_ % 12 < 6 ? kRedColor : kCyanColor;
            accent = "!";
            accent_color = kRedColor;
            break;
        case HensunFaceState::kSleep:
            eye_height = 7;
            mouth_width = 16;
            show_highlights = false;
            accent = "Z z";
            break;
        case HensunFaceState::kLaughing:
            eye_height = 8;
            mouth_width = 58;
            mouth_height = 30 + (pulse % 3) * 3;
            open_mouth = true;
            show_highlights = false;
            show_cheeks = true;
            accent = "HA!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kFunny:
            left_eye_height = 18;
            right_eye_height = 64;
            pupil_shift = (pulse < 10) ? -4 : 4;
            mouth_width = 42;
            mouth_x = 8;
            accent = "~";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kLoving:
            eye_height = 58;
            mouth_width = 42;
            show_cheeks = true;
            accent = "<3";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kEmbarrassed:
            eye_height = 22;
            eye_y = -10;
            mouth_width = 22;
            pupil_shift = -3;
            show_cheeks = true;
            accent = "..";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kConfident:
            eye_height = 28;
            brow_y = -52;
            mouth_width = 38;
            mouth_x = 7;
            accent = "^";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kDelicious:
            eye_height = 9;
            mouth_width = 46;
            mouth_height = 16;
            open_mouth = true;
            show_highlights = false;
            show_cheeks = true;
            accent = "YUM";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kSad:
            eye_height = 44;
            eye_y = -10;
            brow_y = -58;
            mouth_width = 30;
            accent = ".";
            break;
        case HensunFaceState::kCrying:
            eye_height = 38;
            eye_y = -8;
            mouth_width = 24;
            accent = ";;";
            accent_color = kCyanColor;
            break;
        case HensunFaceState::kSleepy:
            eye_height = 10;
            mouth_width = 18;
            mouth_height = 12;
            open_mouth = true;
            show_highlights = false;
            accent = "z";
            break;
        case HensunFaceState::kSilly:
            left_eye_height = 16;
            right_eye_height = 56;
            mouth_width = 24;
            mouth_height = 20;
            mouth_x = 10;
            open_mouth = true;
            accent = ":P";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kAngry:
            eye_height = 30;
            brow_y = -48;
            mouth_width = 42;
            main_color = kRedColor;
            accent = "!!";
            accent_color = kRedColor;
            break;
        case HensunFaceState::kSurprised:
            eye_width = 54;
            eye_height = 76;
            mouth_width = 24;
            mouth_height = 28;
            open_mouth = true;
            accent = "!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kShocked:
            eye_width = 58;
            eye_height = 80;
            mouth_width = 32;
            mouth_height = 36;
            open_mouth = true;
            main_color = animation_frame_ % 10 < 5 ? kCyanColor : kWhiteColor;
            accent = "!!!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kWinking:
            left_eye_height = 7;
            right_eye_height = 66;
            mouth_width = 38;
            show_cheeks = true;
            accent = "*";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kRelaxed:
            eye_height = 12;
            eye_y = -12;
            mouth_width = 36;
            show_highlights = false;
            accent = "~";
            break;
        case HensunFaceState::kConfused:
            left_eye_height = 64;
            right_eye_height = 42;
            pupil_shift = 5;
            mouth_width = 15;
            mouth_x = -9;
            accent = "?";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kProud:
            eye_height = 20;
            brow_y = -48;
            mouth_width = 42;
            mouth_x = 8;
            accent = "+";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kExcited:
            eye_width = 54 + std::min(pulse, 20 - pulse) / 2;
            eye_height = 70 + std::min(pulse, 20 - pulse) / 2;
            mouth_width = 40;
            mouth_height = 24;
            open_mouth = true;
            show_cheeks = true;
            accent = "**";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kWorried:
            eye_height = 48;
            brow_y = -60;
            mouth_width = 24;
            accent = "...";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kApology:
            eye_height = 34;
            eye_y = -8;
            mouth_width = 22;
            show_cheeks = true;
            accent = "SORRY";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kPairing:
            eye_height = 60;
            pupil_shift = static_cast<int>((animation_frame_ / 5) % 7) - 3;
            mouth_width = 18;
            accent = "<>";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kNetworkOk:
            eye_height = 56;
            mouth_width = 42;
            accent = "WIFI";
            accent_color = kCyanColor;
            break;
        case HensunFaceState::kUpdating:
            eye_height = 36;
            pupil_shift = static_cast<int>((animation_frame_ / 3) % 7) - 3;
            mouth_width = 14;
            accent = "%";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kSafeBlock:
            eye_height = 34;
            mouth_width = 44;
            main_color = kAmberColor;
            accent = "SAFE";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kCount:
            break;
    }

    left_eye_height = left_eye_height == 0 ? eye_height : left_eye_height;
    right_eye_height = right_eye_height == 0 ? eye_height : right_eye_height;
    const int left_glow_height = left_eye_height + (left_eye_height > 12 ? 12 : 5);
    const int right_glow_height = right_eye_height + (right_eye_height > 12 ? 12 : 5);
    Place(left_eye_glow_, eye_width + 12, left_glow_height, -eye_x, eye_y);
    Place(right_eye_glow_, eye_width + 12, right_glow_height, eye_x, eye_y);
    Place(left_eye_, eye_width, left_eye_height, -eye_x, eye_y);
    Place(right_eye_, eye_width, right_eye_height, eye_x, eye_y);
    lv_obj_set_style_bg_color(left_eye_, lv_color_hex(main_color), 0);
    lv_obj_set_style_bg_color(right_eye_, lv_color_hex(main_color), 0);
    lv_obj_set_style_bg_color(left_eye_glow_, lv_color_hex(main_color), 0);
    lv_obj_set_style_bg_color(right_eye_glow_, lv_color_hex(main_color), 0);

    Place(left_highlight_, 12, 17, -eye_x - 9 + pupil_shift, eye_y - 17);
    Place(right_highlight_, 12, 17, eye_x - 9 + pupil_shift, eye_y - 17);
    if (show_highlights) {
        lv_obj_remove_flag(left_highlight_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(right_highlight_, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(left_highlight_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(right_highlight_, LV_OBJ_FLAG_HIDDEN);
    }

    Place(left_brow_, state_ == HensunFaceState::kInterrupted ? 44 : 28, 5, -eye_x, brow_y);
    Place(right_brow_, state_ == HensunFaceState::kInterrupted ? 44 : 28, 5, eye_x, brow_y);
    lv_obj_set_style_bg_color(left_brow_, lv_color_hex(main_color), 0);
    lv_obj_set_style_bg_color(right_brow_, lv_color_hex(main_color), 0);

    Place(mouth_, mouth_width, mouth_height, mouth_x, mouth_y);
    lv_obj_set_style_bg_color(mouth_, lv_color_hex(open_mouth ? kBackgroundColor : main_color), 0);
    lv_obj_set_style_border_width(mouth_, open_mouth ? 4 : 0, 0);
    lv_obj_set_style_border_color(mouth_, lv_color_hex(main_color), 0);

    Place(left_cheek_, 22, 7, -72, 47);
    Place(right_cheek_, 22, 7, 72, 47);
    if (show_cheeks) {
        lv_obj_remove_flag(left_cheek_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(right_cheek_, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(left_cheek_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(right_cheek_, LV_OBJ_FLAG_HIDDEN);
    }

    lv_label_set_text(accent_label_, accent);
    lv_obj_set_style_text_color(accent_label_, lv_color_hex(accent_color), 0);
    lv_obj_set_style_text_opa(accent_label_, static_cast<lv_opa_t>(150 + pulse * 5), 0);

    const int64_t render_us = esp_timer_get_time() - started_us;
    render_total_us_ += render_us;
    render_max_us_ = std::max(render_max_us_, render_us);
    ++render_samples_;
    if (render_samples_ >= 100) {
        ESP_LOGI(kTag, "LVGL face update avg=%lld us max=%lld us state=%s",
                 render_total_us_ / render_samples_, render_max_us_, StateName(state_));
        render_samples_ = 0;
        render_total_us_ = 0;
        render_max_us_ = 0;
    }
}
