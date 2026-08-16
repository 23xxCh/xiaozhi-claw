#include "hensun_face_display.h"

#include "hensun_face_assets.h"

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
constexpr uint32_t kEntryAnimationFrames = 8;
constexpr uint32_t kSpeechLevelStaleMs = 180;
constexpr uint8_t kSpeechAttackPerFrame = 28;
constexpr uint8_t kSpeechReleasePerFrame = 14;
constexpr uint8_t kBlinkClosedFrames = 3;
constexpr uint32_t kBlinkMinIntervalFrames = 70;
constexpr uint32_t kBlinkIntervalRangeFrames = 71;
constexpr uint32_t kGazeMinIntervalFrames = 40;
constexpr uint32_t kGazeIntervalRangeFrames = 41;
constexpr uint32_t kShowcaseStateFrames = 24;  // 1.2 seconds at 20 FPS.
constexpr uint8_t kShowcaseSceneCount = 60;
static_assert(kShowcaseSceneCount == static_cast<uint8_t>(HensunFaceState::kCount));

constexpr uint32_t kBackgroundColor = 0x020811;
constexpr uint32_t kCyanColor = 0x49F6FF;
constexpr uint32_t kWhiteColor = 0xF4FFFF;
constexpr uint32_t kPinkColor = 0xFF6FB7;
constexpr uint32_t kAmberColor = 0xFFC247;
constexpr uint32_t kRedColor = 0xFF4D67;
constexpr uint32_t kMintColor = 0x4DFFBE;
constexpr uint32_t kBlueColor = 0x66A3FF;

struct FaceRoute {
    const char* input;
    HensunFaceState state;
    uint32_t hold_ms;
};

enum class MouthStyle : uint8_t {
    kSmile,
    kFlat,
    kOpen,
    kFrown,
};

enum class MotionFamily : uint8_t {
    kCalm,
    kListen,
    kThink,
    kSpeak,
    kCelebrate,
    kSleep,
    kAlert,
    kStatus,
    kRestrained,
};

struct FaceMotion {
    int face_x = 0;
    int face_y = 0;
    int eye_shift_x = 0;
    int mouth_y = 0;
    int cheek_y = 0;
    int symbol_x = 0;
    int symbol_y = 0;
    uint16_t symbol_scale = LV_SCALE_NONE;
    int16_t symbol_rotation = 0;
    lv_opa_t symbol_opacity = 230;
};

// Canonical names are sent by the Hensun cloud in llm.emotion or alert.emotion.
// Compatibility aliases cover emotion names already emitted by XiaoZhi firmware/services.
constexpr FaceRoute kFaceRoutes[] = {
    {"listening_started", HensunFaceState::kListeningStarted, 45000},
    {"positive_response", HensunFaceState::kPositiveResponse, 4000},
    {"clarification_needed", HensunFaceState::kClarificationNeeded, 12000},
    {"processing_started", HensunFaceState::kProcessingStarted, 20000},
    {"confirmation_required", HensunFaceState::kConfirmationRequired, 15000},
    {"asr_low_confidence", HensunFaceState::kAsrLowConfidence, 7000},
    {"noisy_environment", HensunFaceState::kNoisyEnvironment, 6000},
    {"user_continue_expected", HensunFaceState::kUserContinueExpected, 6000},
    {"user_interrupted_assistant", HensunFaceState::kUserInterruptedAssistant, 800},
    {"boot_ready", HensunFaceState::kBootReady, 2500},
    {"wake_word_detected", HensunFaceState::kWakeWordDetected, 1200},
    {"idle_entered", HensunFaceState::kIdleEntered, 3000},
    {"pairing_mode_entered", HensunFaceState::kPairingModeEntered, 15000},
    {"network_connected", HensunFaceState::kNetworkConnected, 3000},
    {"charging_started", HensunFaceState::kChargingStarted, 10000},
    {"charge_complete", HensunFaceState::kChargeComplete, 3000},
    {"sleep_entered", HensunFaceState::kSleepEntered, 3000},
    {"mild_amusement", HensunFaceState::kMildAmusement, 2200},
    {"strong_amusement", HensunFaceState::kStrongAmusement, 3200},
    {"positive_surprise", HensunFaceState::kPositiveSurprise, 2200},
    {"compliment_received", HensunFaceState::kComplimentReceived, 3000},
    {"achievement_celebration", HensunFaceState::kAchievementCelebration, 4000},
    {"encouragement_requested", HensunFaceState::kEncouragementRequested, 4000},
    {"thanks_received", HensunFaceState::kThanksReceived, 3500},
    {"affection_received", HensunFaceState::kAffectionReceived, 3500},
    {"curiosity_engaged", HensunFaceState::kCuriosityEngaged, 5000},
    {"sadness_detected", HensunFaceState::kSadnessDetected, 5000},
    {"comfort_mode_entered", HensunFaceState::kComfortModeEntered, 8000},
    {"worry_detected", HensunFaceState::kWorryDetected, 5000},
    {"anger_detected", HensunFaceState::kAngerDetected, 6000},
    {"fear_detected", HensunFaceState::kFearDetected, 6000},
    {"loneliness_detected", HensunFaceState::kLonelinessDetected, 8000},
    {"fatigue_detected", HensunFaceState::kFatigueDetected, 5000},
    {"unfairness_distress", HensunFaceState::kUnfairnessDistress, 8000},
    {"disappointment_detected", HensunFaceState::kDisappointmentDetected, 6000},
    {"assistant_apology_required", HensunFaceState::kAssistantApologyRequired, 5000},
    {"first_interaction", HensunFaceState::kFirstInteraction, 4000},
    {"morning_greeting", HensunFaceState::kMorningGreeting, 4000},
    {"noon_greeting", HensunFaceState::kNoonGreeting, 4000},
    {"bedtime_greeting", HensunFaceState::kBedtimeGreeting, 4000},
    {"return_after_absence", HensunFaceState::kReturnAfterAbsence, 4500},
    {"birthday_greeting", HensunFaceState::kBirthdayGreeting, 5000},
    {"holiday_greeting", HensunFaceState::kHolidayGreeting, 5000},
    {"meal_check_in", HensunFaceState::kMealCheckIn, 5000},
    {"reminder_created", HensunFaceState::kReminderCreated, 3000},
    {"reminder_due", HensunFaceState::kReminderDue, 6000},
    {"timer_started", HensunFaceState::kTimerStarted, 3000},
    {"timer_finished", HensunFaceState::kTimerFinished, 6000},
    {"alarm_triggered", HensunFaceState::kAlarmTriggered, 6000},
    {"volume_changed", HensunFaceState::kVolumeChanged, 2500},
    {"mode_changed", HensunFaceState::kModeChanged, 2500},
    {"query_result_ready", HensunFaceState::kQueryResultReady, 3500},
    {"network_unavailable", HensunFaceState::kNetworkUnavailable, 6000},
    {"cloud_service_unavailable", HensunFaceState::kCloudServiceUnavailable, 6000},
    {"battery_low", HensunFaceState::kBatteryLow, 6000},
    {"battery_critical", HensunFaceState::kBatteryCritical, 10000},
    {"device_overheat", HensunFaceState::kDeviceOverheat, 10000},
    {"microphone_fault", HensunFaceState::kMicrophoneFault, 8000},
    {"content_safety_blocked", HensunFaceState::kContentSafetyBlocked, 6000},
    {"user_crisis_detected", HensunFaceState::kUserCrisisDetected, 10000},

    {"ready", HensunFaceState::kBootReady, 3000},
    {"idle", HensunFaceState::kIdleEntered, 3000},
    {"listening", HensunFaceState::kListeningStarted, 3000},
    {"thinking", HensunFaceState::kProcessingStarted, 5000},
    {"speaking", HensunFaceState::kQueryResultReady, 3000},
    {"interrupted", HensunFaceState::kUserInterruptedAssistant, 2500},
    {"happy", HensunFaceState::kPositiveResponse, 5000},
    {"curious", HensunFaceState::kCuriosityEngaged, 5000},
    {"caring", HensunFaceState::kComfortModeEntered, 5000},
    {"reminder", HensunFaceState::kReminderDue, 6000},
    {"alarm", HensunFaceState::kAlarmTriggered, 6000},
    {"timer_done", HensunFaceState::kTimerFinished, 6000},
    {"network_error", HensunFaceState::kNetworkUnavailable, 5000},
    {"cloud_off", HensunFaceState::kNetworkUnavailable, 5000},
    {"cancel", HensunFaceState::kNetworkUnavailable, 5000},
    {"sleep", HensunFaceState::kSleepEntered, 8000},
    {"laughing", HensunFaceState::kStrongAmusement, 5000},
    {"funny", HensunFaceState::kMildAmusement, 5000},
    {"loving", HensunFaceState::kAffectionReceived, 5000},
    {"kissy", HensunFaceState::kAffectionReceived, 5000},
    {"embarrassed", HensunFaceState::kComplimentReceived, 5000},
    {"confident", HensunFaceState::kEncouragementRequested, 5000},
    {"cool", HensunFaceState::kEncouragementRequested, 5000},
    {"delicious", HensunFaceState::kMealCheckIn, 5000},
    {"sad", HensunFaceState::kSadnessDetected, 5000},
    {"crying", HensunFaceState::kUnfairnessDistress, 5000},
    {"sleepy", HensunFaceState::kFatigueDetected, 5000},
    {"silly", HensunFaceState::kMildAmusement, 5000},
    {"angry", HensunFaceState::kAngerDetected, 5000},
    {"surprised", HensunFaceState::kPositiveSurprise, 5000},
    {"shocked", HensunFaceState::kPositiveSurprise, 5000},
    {"winking", HensunFaceState::kFirstInteraction, 5000},
    {"relaxed", HensunFaceState::kComfortModeEntered, 5000},
    {"confused", HensunFaceState::kClarificationNeeded, 5000},
    {"proud", HensunFaceState::kAchievementCelebration, 5000},
    {"excited", HensunFaceState::kReturnAfterAbsence, 5000},
    {"worried", HensunFaceState::kWorryDetected, 5000},
    {"warning", HensunFaceState::kWorryDetected, 5000},
    {"apology", HensunFaceState::kAssistantApologyRequired, 5000},
    {"pairing", HensunFaceState::kPairingModeEntered, 5000},
    {"link", HensunFaceState::kPairingModeEntered, 5000},
    {"network_ok", HensunFaceState::kNetworkConnected, 3000},
    {"robot_2", HensunFaceState::kNetworkConnected, 3000},
    {"updating", HensunFaceState::kModeChanged, 8000},
    {"download", HensunFaceState::kModeChanged, 8000},
    {"cloud_download", HensunFaceState::kModeChanged, 8000},
    {"safe_block", HensunFaceState::kContentSafetyBlocked, 6000},
    {"content_blocked", HensunFaceState::kContentSafetyBlocked, 6000},
    {"safety_block", HensunFaceState::kContentSafetyBlocked, 6000},
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

int TriangleWave(uint32_t frame, uint32_t period, int amplitude) {
    const uint32_t half_period = period / 2;
    const uint32_t phase = frame % period;
    const uint32_t distance = phase <= half_period ? phase : period - phase;
    return -amplitude + static_cast<int>(distance * amplitude * 2 / half_period);
}

int EaseOutEntry(uint32_t frame, int initial_offset) {
    if (frame >= kEntryAnimationFrames) {
        return 0;
    }
    const uint32_t remaining = kEntryAnimationFrames - frame;
    return static_cast<int>(remaining * remaining * initial_offset /
                            (kEntryAnimationFrames * kEntryAnimationFrames));
}

MotionFamily MotionFamilyForState(HensunFaceState state) {
    switch (state) {
        case HensunFaceState::kListeningStarted: return MotionFamily::kListen;
        case HensunFaceState::kWakeWordDetected: return MotionFamily::kListen;
        case HensunFaceState::kAsrLowConfidence:
        case HensunFaceState::kUserContinueExpected:
            return MotionFamily::kListen;

        case HensunFaceState::kProcessingStarted: return MotionFamily::kThink;
        case HensunFaceState::kClarificationNeeded: return MotionFamily::kThink;
        case HensunFaceState::kConfirmationRequired:
        case HensunFaceState::kNoisyEnvironment:
        case HensunFaceState::kCuriosityEngaged:
            return MotionFamily::kThink;

        case HensunFaceState::kQueryResultReady: return MotionFamily::kSpeak;

        case HensunFaceState::kPositiveResponse: return MotionFamily::kCelebrate;
        case HensunFaceState::kAchievementCelebration: return MotionFamily::kCelebrate;
        case HensunFaceState::kMildAmusement:
        case HensunFaceState::kStrongAmusement:
        case HensunFaceState::kPositiveSurprise:
        case HensunFaceState::kComplimentReceived:
        case HensunFaceState::kEncouragementRequested:
        case HensunFaceState::kThanksReceived:
        case HensunFaceState::kAffectionReceived:
        case HensunFaceState::kFirstInteraction:
        case HensunFaceState::kReturnAfterAbsence:
        case HensunFaceState::kBirthdayGreeting:
        case HensunFaceState::kHolidayGreeting:
            return MotionFamily::kCelebrate;

        case HensunFaceState::kSleepEntered: return MotionFamily::kSleep;
        case HensunFaceState::kFatigueDetected:
        case HensunFaceState::kBedtimeGreeting:
            return MotionFamily::kSleep;

        case HensunFaceState::kAlarmTriggered: return MotionFamily::kAlert;
        case HensunFaceState::kReminderDue: return MotionFamily::kAlert;
        case HensunFaceState::kUserInterruptedAssistant:
        case HensunFaceState::kAngerDetected:
        case HensunFaceState::kNetworkUnavailable:
        case HensunFaceState::kCloudServiceUnavailable:
        case HensunFaceState::kBatteryCritical:
        case HensunFaceState::kDeviceOverheat:
        case HensunFaceState::kMicrophoneFault:
            return MotionFamily::kAlert;

        case HensunFaceState::kPairingModeEntered: return MotionFamily::kStatus;
        case HensunFaceState::kChargingStarted: return MotionFamily::kStatus;
        case HensunFaceState::kBootReady:
        case HensunFaceState::kNetworkConnected:
        case HensunFaceState::kChargeComplete:
        case HensunFaceState::kMealCheckIn:
        case HensunFaceState::kReminderCreated:
        case HensunFaceState::kTimerStarted:
        case HensunFaceState::kTimerFinished:
        case HensunFaceState::kVolumeChanged:
        case HensunFaceState::kModeChanged:
        case HensunFaceState::kBatteryLow:
            return MotionFamily::kStatus;

        case HensunFaceState::kContentSafetyBlocked: return MotionFamily::kRestrained;
        case HensunFaceState::kUserCrisisDetected: return MotionFamily::kRestrained;
        case HensunFaceState::kSadnessDetected:
        case HensunFaceState::kWorryDetected:
        case HensunFaceState::kFearDetected:
        case HensunFaceState::kLonelinessDetected:
        case HensunFaceState::kUnfairnessDistress:
        case HensunFaceState::kDisappointmentDetected:
        case HensunFaceState::kAssistantApologyRequired:
            return MotionFamily::kRestrained;

        case HensunFaceState::kIdleEntered:
        case HensunFaceState::kComfortModeEntered:
        case HensunFaceState::kMorningGreeting:
        case HensunFaceState::kNoonGreeting:
            return MotionFamily::kCalm;
        case HensunFaceState::kCount:
            return MotionFamily::kRestrained;
    }
    return MotionFamily::kRestrained;
}

FaceMotion MotionForState(HensunFaceState state, uint32_t frame) {
    FaceMotion motion;
    const bool entering = frame < kEntryAnimationFrames;
    if (entering) {
        motion.face_y = EaseOutEntry(frame, 6);
        motion.symbol_scale = static_cast<uint16_t>(256 - EaseOutEntry(frame, 32));
        motion.symbol_opacity = static_cast<lv_opa_t>(230 - EaseOutEntry(frame, 55));
    }

    switch (MotionFamilyForState(state)) {
        case MotionFamily::kCalm:
            motion.face_y += TriangleWave(frame, 80, 1);
            if (!entering) {
                motion.symbol_scale = static_cast<uint16_t>(256 + TriangleWave(frame, 64, 3));
            }
            break;
        case MotionFamily::kListen:
            motion.face_y += TriangleWave(frame, 40, 1);
            motion.eye_shift_x = TriangleWave(frame, 48, 2);
            if (!entering) {
                motion.symbol_scale = static_cast<uint16_t>(256 + TriangleWave(frame, 32, 8));
                motion.symbol_opacity = static_cast<lv_opa_t>(230 + TriangleWave(frame, 40, 20));
            }
            break;
        case MotionFamily::kThink:
            motion.eye_shift_x = TriangleWave(frame, 32, 4);
            motion.symbol_y = TriangleWave(frame, 40, 2);
            motion.symbol_rotation = static_cast<int16_t>(TriangleWave(frame, 48, 30));
            break;
        case MotionFamily::kSpeak:
            motion.face_y += TriangleWave(frame, 16, 1);
            motion.mouth_y = TriangleWave(frame, 8, 2);
            if (!entering) {
                motion.symbol_scale = static_cast<uint16_t>(256 + TriangleWave(frame, 16, 5));
            }
            break;
        case MotionFamily::kCelebrate: {
            const int lift = (TriangleWave(frame, 20, 2) + 2) / 2;
            motion.face_y -= lift;
            motion.cheek_y = -lift;
            motion.symbol_y = -lift;
            motion.symbol_rotation = static_cast<int16_t>(TriangleWave(frame, 24, 40));
            if (!entering) {
                motion.symbol_scale = static_cast<uint16_t>(264 + TriangleWave(frame, 20, 8));
            }
            break;
        }
        case MotionFamily::kSleep:
            motion.face_y += TriangleWave(frame, 80, 1);
            motion.symbol_y = TriangleWave(frame, 80, 2);
            if (!entering) {
                motion.symbol_scale = static_cast<uint16_t>(256 + TriangleWave(frame, 80, 3));
            }
            break;
        case MotionFamily::kAlert:
            motion.face_x = ((frame / 2) % 2 == 0) ? -2 : 2;
            motion.symbol_x = -motion.face_x;
            motion.symbol_rotation = static_cast<int16_t>(motion.face_x < 0 ? -60 : 60);
            if (!entering) {
                motion.symbol_scale = 264;
            }
            break;
        case MotionFamily::kStatus:
            if (!entering) {
                motion.symbol_scale = static_cast<uint16_t>(256 + TriangleWave(frame, 40, 6));
                motion.symbol_opacity = static_cast<lv_opa_t>(235 + TriangleWave(frame, 40, 15));
            }
            break;
        case MotionFamily::kRestrained:
            motion.symbol_opacity = entering ? motion.symbol_opacity : 230;
            break;
    }
    return motion;
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

const lv_image_dsc_t* HensunFaceSceneImage(HensunFaceState state) {
    return HensunFaceSceneImageByIndex(static_cast<uint8_t>(state));
}

uint32_t HensunFaceSceneAccentRgb(HensunFaceState state) {
    return HensunFaceSceneAccentRgbByIndex(static_cast<uint8_t>(state));
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
    ESP_LOGI(kTag,
             "Company-derived 60-scene face ready with audio-reactive animation at 20 FPS");
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
    mouth_arc_ = lv_arc_create(face_layer_);
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
    lv_obj_remove_style_all(mouth_arc_);
    lv_obj_remove_flag(mouth_arc_, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(mouth_arc_, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_arc_width(mouth_arc_, 6, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(mouth_arc_, true, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(mouth_arc_, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(mouth_arc_, LV_OPA_TRANSP, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(mouth_arc_, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_add_flag(mouth_arc_, LV_OBJ_FLAG_HIDDEN);
    StyleShape(left_cheek_, kPinkColor, LV_OPA_80);
    StyleShape(right_cheek_, kPinkColor, LV_OPA_80);

    symbol_image_ = lv_image_create(face_layer_);
    lv_obj_remove_flag(symbol_image_, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(symbol_image_, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(symbol_image_, LV_OBJ_FLAG_HIDDEN);
    lv_obj_align(symbol_image_, LV_ALIGN_CENTER, 72, -78);
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
        ShowTransientStateLocked(HensunFaceState::kNetworkConnected, 3000, "notification",
                                 "network_connected");
    } else if (StartsWith(notification, Lang::Strings::CONNECT_TO) ||
               std::strcmp(notification, Lang::Strings::SCANNING_WIFI) == 0 ||
               std::strcmp(notification, Lang::Strings::ENTERING_WIFI_CONFIG_MODE) == 0) {
        ShowTransientStateLocked(HensunFaceState::kPairingModeEntered, 3000, "notification",
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
    SetFaceStateLocked(HensunFaceState::kListeningStarted);
    if (status_label_ != nullptr) {
        lv_label_set_text(status_label_, "01 LISTEN");
    }
    ESP_LOGI(kTag, "Starting 60-scene display showcase");
}

void HensunFaceDisplay::SetSpeechLevel(uint8_t level) {
    speech_level_.store(std::min<uint8_t>(100, level));
    speech_level_updated_ms_.store(static_cast<uint32_t>(esp_timer_get_time() / 1000));
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
            if (next >= kShowcaseSceneCount) {
                showcase_active_ = false;
                SetFaceStateLocked(StateFromDevice());
                if (status_label_ != nullptr) {
                    lv_label_set_text(status_label_,
                                      last_status_.empty() ? StateName(state_) : last_status_.c_str());
                }
                ESP_LOGI(kTag, "60-scene display showcase complete");
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

    UpdateSpeechEnvelope();
    UpdateAmbientMotion();
    ++animation_frame_;
    RenderFace();
}

void HensunFaceDisplay::UpdateSpeechEnvelope() {
    const uint32_t now_ms = static_cast<uint32_t>(esp_timer_get_time() / 1000);
    const uint32_t updated_ms = speech_level_updated_ms_.load();
    const uint8_t target = updated_ms != 0 && now_ms - updated_ms <= kSpeechLevelStaleMs
                               ? speech_level_.load()
                               : 0;

    if (target > speech_level_smoothed_) {
        const uint8_t difference = target - speech_level_smoothed_;
        speech_level_smoothed_ += std::min<uint8_t>(difference, kSpeechAttackPerFrame);
    } else {
        const uint8_t difference = speech_level_smoothed_ - target;
        speech_level_smoothed_ -= std::min<uint8_t>(difference, kSpeechReleasePerFrame);
    }
}

uint32_t HensunFaceDisplay::NextPseudoRandom() {
    pseudo_random_state_ = pseudo_random_state_ * 1664525u + 1013904223u;
    return pseudo_random_state_;
}

void HensunFaceDisplay::UpdateAmbientMotion() {
    ++ambient_frame_;

    if (blink_frames_remaining_ > 0) {
        --blink_frames_remaining_;
    }
    if (ambient_frame_ >= next_blink_frame_) {
        blink_frames_remaining_ = kBlinkClosedFrames;
        next_blink_frame_ = ambient_frame_ + kBlinkMinIntervalFrames +
                            NextPseudoRandom() % kBlinkIntervalRangeFrames;
    }

    const MotionFamily family = MotionFamilyForState(state_);
    const bool allow_gaze = family == MotionFamily::kCalm ||
                            family == MotionFamily::kListen ||
                            family == MotionFamily::kSpeak ||
                            family == MotionFamily::kCelebrate ||
                            family == MotionFamily::kStatus;
    if (!allow_gaze) {
        gaze_target_x_ = 0;
    } else if (ambient_frame_ >= next_gaze_frame_) {
        gaze_target_x_ = static_cast<int8_t>(NextPseudoRandom() % 5) - 2;
        next_gaze_frame_ = ambient_frame_ + kGazeMinIntervalFrames +
                           NextPseudoRandom() % kGazeIntervalRangeFrames;
    }

    if (gaze_x_ < gaze_target_x_) {
        ++gaze_x_;
    } else if (gaze_x_ > gaze_target_x_) {
        --gaze_x_;
    }
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
            return HensunFaceState::kBootReady;
        case kDeviceStateWifiConfiguring:
        case kDeviceStateActivating:
        case kDeviceStateConnecting:
            return HensunFaceState::kPairingModeEntered;
        case kDeviceStateIdle:
            return HensunFaceState::kIdleEntered;
        case kDeviceStateListening:
        case kDeviceStateAudioTesting:
            return HensunFaceState::kListeningStarted;
        case kDeviceStateSpeaking:
            return HensunFaceState::kQueryResultReady;
        case kDeviceStateUpgrading:
            return HensunFaceState::kModeChanged;
        case kDeviceStateFatalError:
            return HensunFaceState::kNetworkUnavailable;
        default:
            return HensunFaceState::kBootReady;
    }
}

const char* HensunFaceDisplay::StateName(HensunFaceState state) {
    static constexpr const char* kNames[] = {
        "LISTEN", "HAPPY", "CLARIFY", "THINK", "CONFIRM", "HEAR AGAIN",
        "NOISY", "CONTINUE", "INTERRUPT", "READY", "WAKE", "IDLE",
        "PAIRING", "NET OK", "CHARGING", "CHARGED", "SLEEP", "CHUCKLE",
        "LAUGH", "SURPRISE", "SHY", "ACHIEVE", "COURAGE", "THANKS",
        "LOVE", "CURIOUS", "SAD", "COMFORT", "WORRY", "CALM DOWN",
        "FEAR CARE", "COMPANY", "TIRED", "EMPATHY", "REGRET", "APOLOGY",
        "HELLO", "MORNING", "NOON", "GOOD NIGHT", "WELCOME", "BIRTHDAY",
        "HOLIDAY", "MEAL", "REMINDER OK", "REMINDER", "TIMER ON", "TIMER DONE",
        "ALARM", "VOLUME", "MODE", "RESULT", "NET ERROR", "CLOUD BUSY",
        "LOW POWER", "POWER STOP", "OVERHEAT", "MIC ERROR", "SAFE BLOCK", "CRISIS CARE",
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
    const bool blink = blink_frames_remaining_ > 0;
    const FaceMotion motion = MotionForState(state_, animation_frame_);

    int eye_width = 50;
    int eye_height = 68;
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
    int left_brow_rotation = 0;
    int right_brow_rotation = 0;
    uint32_t main_color = kCyanColor;
    uint32_t accent_color = HensunFaceSceneAccentRgb(state_);
    const char* accent = "";
    bool show_highlights = !blink;
    bool show_cheeks = false;
    bool open_mouth = false;
    MouthStyle mouth_style = MouthStyle::kSmile;

    switch (state_) {
        case HensunFaceState::kBootReady:
            eye_height = 62;
            accent = "*";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kIdleEntered:
            mouth_width = 24;
            break;
        case HensunFaceState::kListeningStarted:
            eye_width = 52 + std::min(pulse, 20 - pulse) / 2;
            eye_height = 70 + std::min(pulse, 20 - pulse) / 2;
            accent = "))";
            break;
        case HensunFaceState::kProcessingStarted:
            pupil_shift = static_cast<int>((animation_frame_ / 8) % 5) - 2;
            mouth_width = 12;
            mouth_style = MouthStyle::kFlat;
            accent = "...";
            break;
        case HensunFaceState::kQueryResultReady:
            eye_height = 58;
            mouth_width = 30 + (pulse % 5) * 3;
            mouth_height = 12 + (pulse % 4) * 5;
            open_mouth = true;
            break;
        case HensunFaceState::kUserInterruptedAssistant:
            eye_height = 42;
            brow_y = -56;
            mouth_width = 30;
            mouth_style = MouthStyle::kFlat;
            accent = "!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kPositiveResponse:
            eye_height = 10;
            mouth_width = 48;
            mouth_height = 9;
            show_highlights = false;
            show_cheeks = true;
            accent = "*";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kCuriosityEngaged:
            eye_height = 72;
            pupil_shift = 5;
            mouth_width = 13;
            mouth_height = 13;
            open_mouth = true;
            accent = "?";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kComfortModeEntered:
            eye_height = 56;
            eye_y = -14;
            mouth_width = 40;
            show_cheeks = true;
            accent = "*";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kReminderDue:
            eye_height = 64;
            mouth_width = 18;
            mouth_height = 16;
            open_mouth = true;
            accent = "!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kNetworkUnavailable:
            eye_height = 28;
            mouth_width = 38;
            mouth_style = MouthStyle::kFrown;
            main_color = animation_frame_ % 12 < 6 ? kRedColor : kCyanColor;
            accent = "!";
            accent_color = kRedColor;
            break;
        case HensunFaceState::kSleepEntered:
            eye_height = 7;
            mouth_width = 16;
            show_highlights = false;
            accent = "Z z";
            break;
        case HensunFaceState::kStrongAmusement:
            eye_height = 8;
            mouth_width = 58;
            mouth_height = 30 + (pulse % 3) * 3;
            open_mouth = true;
            show_highlights = false;
            show_cheeks = true;
            accent = "HA!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kMildAmusement:
            left_eye_height = 18;
            right_eye_height = 64;
            pupil_shift = (pulse < 10) ? -4 : 4;
            mouth_width = 42;
            mouth_x = 8;
            accent = "~";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kAffectionReceived:
            eye_height = 58;
            mouth_width = 42;
            show_cheeks = true;
            accent = "<3";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kComplimentReceived:
            eye_height = 22;
            eye_y = -10;
            mouth_width = 22;
            pupil_shift = -3;
            show_cheeks = true;
            accent = "..";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kEncouragementRequested:
            eye_height = 28;
            brow_y = -52;
            mouth_width = 38;
            mouth_x = 7;
            accent = "^";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kMealCheckIn:
            eye_height = 9;
            mouth_width = 46;
            mouth_height = 16;
            open_mouth = true;
            show_highlights = false;
            show_cheeks = true;
            accent = "YUM";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kSadnessDetected:
            eye_height = 44;
            eye_y = -10;
            brow_y = -58;
            mouth_width = 30;
            mouth_style = MouthStyle::kFrown;
            accent = ".";
            break;
        case HensunFaceState::kUnfairnessDistress:
            eye_height = 38;
            eye_y = -8;
            mouth_width = 24;
            mouth_style = MouthStyle::kFrown;
            accent = ";;";
            accent_color = kCyanColor;
            break;
        case HensunFaceState::kFatigueDetected:
            eye_height = 10;
            mouth_width = 18;
            mouth_height = 12;
            open_mouth = true;
            show_highlights = false;
            accent = "z";
            break;
        case HensunFaceState::kFirstInteraction:
            left_eye_height = 7;
            right_eye_height = 66;
            mouth_width = 38;
            show_cheeks = true;
            accent = "HI";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kAngerDetected:
            eye_height = 10;
            eye_y = -12;
            mouth_width = 36;
            show_highlights = false;
            main_color = kBlueColor;
            accent = "~~~";
            accent_color = kBlueColor;
            break;
        case HensunFaceState::kPositiveSurprise:
            eye_width = 54;
            eye_height = 76;
            mouth_width = 24;
            mouth_height = 28;
            open_mouth = true;
            accent = "!";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kUserCrisisDetected:
            eye_width = 48;
            eye_height = 58;
            eye_y = -12;
            brow_y = -60;
            mouth_width = 38;
            mouth_style = MouthStyle::kFlat;
            accent = "";
            break;
        case HensunFaceState::kThanksReceived:
            left_eye_height = 7;
            right_eye_height = 66;
            mouth_width = 38;
            show_cheeks = true;
            accent = "*";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kBedtimeGreeting:
            eye_height = 12;
            eye_y = -12;
            mouth_width = 36;
            show_highlights = false;
            accent = "~";
            break;
        case HensunFaceState::kClarificationNeeded:
            left_eye_height = 64;
            right_eye_height = 42;
            pupil_shift = 5;
            mouth_width = 15;
            mouth_height = 15;
            mouth_x = -9;
            open_mouth = true;
            accent = "?";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kAchievementCelebration:
            eye_height = 20;
            brow_y = -48;
            mouth_width = 42;
            mouth_x = 8;
            accent = "+";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kReturnAfterAbsence:
            eye_width = 54 + std::min(pulse, 20 - pulse) / 2;
            eye_height = 70 + std::min(pulse, 20 - pulse) / 2;
            mouth_width = 40;
            mouth_height = 24;
            open_mouth = true;
            show_cheeks = true;
            accent = "**";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kWorryDetected:
            eye_height = 48;
            brow_y = -60;
            mouth_width = 24;
            mouth_style = MouthStyle::kFrown;
            accent = "...";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kAssistantApologyRequired:
            eye_height = 34;
            eye_y = -8;
            mouth_width = 22;
            mouth_style = MouthStyle::kFrown;
            show_cheeks = true;
            accent = "SORRY";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kPairingModeEntered:
            eye_height = 60;
            pupil_shift = static_cast<int>((animation_frame_ / 5) % 7) - 3;
            mouth_width = 18;
            accent = "<>";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kNetworkConnected:
            eye_height = 56;
            mouth_width = 42;
            accent = "WIFI";
            accent_color = kCyanColor;
            break;
        case HensunFaceState::kModeChanged:
            eye_height = 36;
            pupil_shift = static_cast<int>((animation_frame_ / 3) % 7) - 3;
            mouth_width = 14;
            accent = "%";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kContentSafetyBlocked:
            eye_height = 34;
            mouth_width = 44;
            mouth_style = MouthStyle::kFlat;
            main_color = kBlueColor;
            accent = "SAFE";
            accent_color = kBlueColor;
            break;
        case HensunFaceState::kConfirmationRequired:
            left_eye_height = 52;
            right_eye_height = 68;
            pupil_shift = 3;
            mouth_width = 16;
            mouth_height = 16;
            open_mouth = true;
            accent = "OK?";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kAsrLowConfidence:
            left_eye_height = 14;
            right_eye_height = 50;
            eye_y = -12;
            mouth_width = 28;
            mouth_style = MouthStyle::kFlat;
            accent = "))";
            break;
        case HensunFaceState::kNoisyEnvironment:
            eye_height = 34;
            brow_y = -56;
            mouth_width = 34;
            mouth_style = MouthStyle::kFrown;
            accent = "~~~";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kUserContinueExpected:
            eye_height = 58;
            eye_y = -14;
            mouth_width = 34;
            accent = "...";
            break;
        case HensunFaceState::kWakeWordDetected:
            eye_width = 54 + std::min(pulse, 20 - pulse) / 2;
            eye_height = 72 + std::min(pulse, 20 - pulse) / 2;
            mouth_width = 30;
            accent = "))";
            accent_color = kMintColor;
            break;
        case HensunFaceState::kChargingStarted:
            eye_height = 9;
            mouth_width = 34;
            show_highlights = false;
            main_color = kMintColor;
            accent = "+";
            accent_color = kMintColor;
            break;
        case HensunFaceState::kChargeComplete:
            eye_height = 9;
            mouth_width = 50;
            show_highlights = false;
            show_cheeks = true;
            main_color = kMintColor;
            accent = "FULL";
            accent_color = kMintColor;
            break;
        case HensunFaceState::kFearDetected:
            eye_width = 48;
            eye_height = 60;
            brow_y = -60;
            mouth_width = 22;
            mouth_height = 18;
            open_mouth = true;
            main_color = kMintColor;
            accent = "SHIELD";
            accent_color = kMintColor;
            break;
        case HensunFaceState::kLonelinessDetected:
            eye_height = 54;
            eye_y = -10;
            brow_y = -60;
            mouth_width = 32;
            show_cheeks = true;
            main_color = kMintColor;
            accent = "<3";
            accent_color = kMintColor;
            break;
        case HensunFaceState::kDisappointmentDetected:
            eye_height = 38;
            eye_y = -7;
            brow_y = -56;
            mouth_width = 38;
            mouth_style = MouthStyle::kFrown;
            accent = ".";
            accent_color = kBlueColor;
            break;
        case HensunFaceState::kMorningGreeting:
            eye_height = 9;
            mouth_width = 46;
            show_highlights = false;
            show_cheeks = true;
            accent = "SUN";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kNoonGreeting:
            eye_height = 56;
            eye_y = -12;
            mouth_width = 36;
            accent = "HI";
            accent_color = kBlueColor;
            break;
        case HensunFaceState::kBirthdayGreeting:
            eye_width = 54;
            eye_height = 72;
            mouth_width = 54;
            mouth_height = 30;
            open_mouth = true;
            show_cheeks = true;
            accent = "B-DAY";
            accent_color = kPinkColor;
            break;
        case HensunFaceState::kHolidayGreeting:
            eye_height = 9;
            mouth_width = 52;
            mouth_height = 26;
            open_mouth = true;
            show_highlights = false;
            accent = "**";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kReminderCreated:
            eye_height = 9;
            mouth_width = 42;
            show_highlights = false;
            main_color = kMintColor;
            accent = "OK";
            accent_color = kMintColor;
            break;
        case HensunFaceState::kTimerStarted:
            eye_height = 24;
            brow_y = -50;
            mouth_width = 34;
            accent = "00:01";
            accent_color = kBlueColor;
            break;
        case HensunFaceState::kTimerFinished:
            eye_height = 9;
            mouth_width = 44;
            show_highlights = false;
            main_color = kMintColor;
            accent = "DONE";
            accent_color = kMintColor;
            break;
        case HensunFaceState::kAlarmTriggered:
            eye_width = 54 + std::min(pulse, 20 - pulse) / 2;
            eye_height = 72;
            mouth_width = 38;
            mouth_height = 25;
            open_mouth = true;
            accent = "BELL";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kVolumeChanged:
            eye_height = 58;
            mouth_width = 34;
            accent = ")))";
            accent_color = kBlueColor;
            break;
        case HensunFaceState::kCloudServiceUnavailable:
            eye_height = 26;
            mouth_width = 18;
            mouth_height = 18;
            open_mouth = true;
            accent = "...";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kBatteryLow:
            eye_height = 14;
            eye_y = -8;
            mouth_width = 30;
            mouth_style = MouthStyle::kFrown;
            show_highlights = false;
            main_color = kRedColor;
            accent = "LOW";
            accent_color = kRedColor;
            break;
        case HensunFaceState::kBatteryCritical:
            eye_height = 7;
            mouth_width = 42;
            mouth_style = MouthStyle::kFlat;
            show_highlights = false;
            main_color = animation_frame_ % 16 < 8 ? kRedColor : kAmberColor;
            accent = "STOP";
            accent_color = kRedColor;
            break;
        case HensunFaceState::kDeviceOverheat:
            eye_height = 26;
            brow_y = -48;
            mouth_width = 42;
            mouth_style = MouthStyle::kFlat;
            main_color = kRedColor;
            accent = "TEMP";
            accent_color = kRedColor;
            break;
        case HensunFaceState::kMicrophoneFault:
            left_eye_height = 42;
            right_eye_height = 58;
            pupil_shift = 4;
            mouth_width = 34;
            mouth_style = MouthStyle::kFrown;
            accent = "MIC X";
            accent_color = kAmberColor;
            break;
        case HensunFaceState::kCount:
            break;
    }

    switch (state_) {
        case HensunFaceState::kSadnessDetected:
        case HensunFaceState::kWorryDetected:
        case HensunFaceState::kFearDetected:
        case HensunFaceState::kUnfairnessDistress:
        case HensunFaceState::kDisappointmentDetected:
        case HensunFaceState::kAssistantApologyRequired:
        case HensunFaceState::kNoisyEnvironment:
        case HensunFaceState::kUserCrisisDetected:
        case HensunFaceState::kBatteryLow:
        case HensunFaceState::kMicrophoneFault:
            left_brow_rotation = 120;
            right_brow_rotation = -120;
            break;
        case HensunFaceState::kAchievementCelebration:
        case HensunFaceState::kEncouragementRequested:
        case HensunFaceState::kTimerStarted:
        case HensunFaceState::kQueryResultReady:
        case HensunFaceState::kDeviceOverheat:
        case HensunFaceState::kContentSafetyBlocked:
            left_brow_rotation = -140;
            right_brow_rotation = 140;
            break;
        case HensunFaceState::kClarificationNeeded:
        case HensunFaceState::kConfirmationRequired:
        case HensunFaceState::kCuriosityEngaged:
            right_brow_rotation = -160;
            break;
        default:
            break;
    }
    if (open_mouth) {
        mouth_style = MouthStyle::kOpen;
    }

    // Audio-reactive mouth: real speaker PCM wins over the synthetic scene cadence.
    const bool device_speaking =
        Application::GetInstance().GetDeviceState() == kDeviceStateSpeaking;
    if (device_speaking) {
        const bool restrained = MotionFamilyForState(state_) == MotionFamily::kRestrained;
        const uint8_t effective_level =
            restrained ? std::min<uint8_t>(55, speech_level_smoothed_)
                       : speech_level_smoothed_;
        if (effective_level > 4) {
            mouth_style = MouthStyle::kOpen;
            mouth_width = 28 + effective_level * 12 / 100;
            mouth_height = 7 + effective_level * 22 / 100;
        } else {
            mouth_style = MouthStyle::kFlat;
            mouth_width = 28;
            mouth_height = 7;
        }
    }
    const int mouth_motion_y = device_speaking ? 0 : motion.mouth_y;

    left_eye_height = left_eye_height == 0 ? eye_height : left_eye_height;
    right_eye_height = right_eye_height == 0 ? eye_height : right_eye_height;
    pupil_shift += gaze_x_;
    if (blink) {
        left_eye_height = std::min(left_eye_height, 7);
        right_eye_height = std::min(right_eye_height, 7);
        show_highlights = false;
    }
    const int left_glow_height = left_eye_height + (left_eye_height > 12 ? 12 : 5);
    const int right_glow_height = right_eye_height + (right_eye_height > 12 ? 12 : 5);
    Place(left_eye_glow_, eye_width + 12, left_glow_height,
          -eye_x + motion.face_x + motion.eye_shift_x, eye_y + motion.face_y);
    Place(right_eye_glow_, eye_width + 12, right_glow_height,
          eye_x + motion.face_x + motion.eye_shift_x, eye_y + motion.face_y);
    Place(left_eye_, eye_width, left_eye_height,
          -eye_x + motion.face_x + motion.eye_shift_x, eye_y + motion.face_y);
    Place(right_eye_, eye_width, right_eye_height,
          eye_x + motion.face_x + motion.eye_shift_x, eye_y + motion.face_y);
    lv_obj_set_style_bg_color(left_eye_, lv_color_hex(main_color), 0);
    lv_obj_set_style_bg_color(right_eye_, lv_color_hex(main_color), 0);
    lv_obj_set_style_bg_color(left_eye_glow_, lv_color_hex(main_color), 0);
    lv_obj_set_style_bg_color(right_eye_glow_, lv_color_hex(main_color), 0);
    const lv_opa_t glow_opacity = static_cast<lv_opa_t>(45 + std::min(pulse, 20 - pulse) * 3);
    lv_obj_set_style_bg_opa(left_eye_glow_, glow_opacity, 0);
    lv_obj_set_style_bg_opa(right_eye_glow_, glow_opacity, 0);

    Place(left_highlight_, 12, 17,
          -eye_x - 9 + pupil_shift + motion.face_x + motion.eye_shift_x,
          eye_y - 17 + motion.face_y);
    Place(right_highlight_, 12, 17,
          eye_x - 9 + pupil_shift + motion.face_x + motion.eye_shift_x,
          eye_y - 17 + motion.face_y);
    if (show_highlights) {
        lv_obj_remove_flag(left_highlight_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(right_highlight_, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(left_highlight_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(right_highlight_, LV_OBJ_FLAG_HIDDEN);
    }

    Place(left_brow_, state_ == HensunFaceState::kUserInterruptedAssistant ? 44 : 28, 5,
          -eye_x + motion.face_x + motion.eye_shift_x, brow_y + motion.face_y);
    Place(right_brow_, state_ == HensunFaceState::kUserInterruptedAssistant ? 44 : 28, 5,
          eye_x + motion.face_x + motion.eye_shift_x, brow_y + motion.face_y);
    lv_obj_set_style_bg_color(left_brow_, lv_color_hex(main_color), 0);
    lv_obj_set_style_bg_color(right_brow_, lv_color_hex(main_color), 0);
    lv_obj_set_style_transform_rotation(left_brow_, left_brow_rotation, 0);
    lv_obj_set_style_transform_rotation(right_brow_, right_brow_rotation, 0);

    if (mouth_style == MouthStyle::kSmile || mouth_style == MouthStyle::kFrown) {
        lv_obj_add_flag(mouth_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(mouth_arc_, LV_OBJ_FLAG_HIDDEN);
        Place(mouth_arc_, mouth_width + 8, std::max(26, mouth_height + 20),
              mouth_x + motion.face_x, mouth_y + motion.face_y + mouth_motion_y);
        if (mouth_style == MouthStyle::kSmile) {
            lv_arc_set_bg_angles(mouth_arc_, 25, 155);
        } else {
            lv_arc_set_bg_angles(mouth_arc_, 205, 335);
        }
        lv_obj_set_style_arc_color(mouth_arc_, lv_color_hex(main_color), LV_PART_MAIN);
    } else {
        lv_obj_add_flag(mouth_arc_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(mouth_, LV_OBJ_FLAG_HIDDEN);
        Place(mouth_, mouth_width, mouth_height, mouth_x + motion.face_x,
              mouth_y + motion.face_y + mouth_motion_y);
        lv_obj_set_style_bg_color(
            mouth_, lv_color_hex(mouth_style == MouthStyle::kOpen ? kBackgroundColor : main_color),
            0);
        lv_obj_set_style_border_width(mouth_, mouth_style == MouthStyle::kOpen ? 4 : 0, 0);
        lv_obj_set_style_border_color(mouth_, lv_color_hex(main_color), 0);
    }

    Place(left_cheek_, 22, 7, -72 + motion.face_x,
          47 + motion.face_y + motion.cheek_y);
    Place(right_cheek_, 22, 7, 72 + motion.face_x,
          47 + motion.face_y + motion.cheek_y);
    if (show_cheeks) {
        lv_obj_remove_flag(left_cheek_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_remove_flag(right_cheek_, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(left_cheek_, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(right_cheek_, LV_OBJ_FLAG_HIDDEN);
    }

    const lv_image_dsc_t* scene_image = HensunFaceSceneImage(state_);
    if (scene_image == nullptr) {
        current_symbol_image_ = nullptr;
        lv_obj_add_flag(symbol_image_, LV_OBJ_FLAG_HIDDEN);
    } else {
        if (current_symbol_image_ != scene_image) {
            current_symbol_image_ = scene_image;
            lv_image_set_src(symbol_image_, scene_image);
        }
        lv_obj_set_style_image_recolor(symbol_image_, lv_color_hex(accent_color), 0);
        lv_obj_set_style_image_recolor_opa(symbol_image_, LV_OPA_COVER, 0);
        lv_obj_set_style_image_opa(symbol_image_, motion.symbol_opacity, 0);
        lv_image_set_scale(symbol_image_, motion.symbol_scale);
        lv_image_set_rotation(symbol_image_, motion.symbol_rotation);
        lv_obj_align(symbol_image_, LV_ALIGN_CENTER,
                     72 + motion.face_x + motion.symbol_x,
                     -78 + motion.face_y + motion.symbol_y);
        lv_obj_remove_flag(symbol_image_, LV_OBJ_FLAG_HIDDEN);
    }

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
