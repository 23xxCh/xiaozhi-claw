#ifndef HENSUN_FACE_DISPLAY_H
#define HENSUN_FACE_DISPLAY_H

#include "display/lcd_display.h"

#include <cstdint>
#include <memory>
#include <string>

enum class HensunFaceState : uint8_t {
    kReady = 0,
    kIdle,
    kListening,
    kThinking,
    kSpeaking,
    kInterrupted,
    kHappy,
    kCurious,
    kCaring,
    kReminder,
    kNetworkError,
    kSleep,
    kLaughing,
    kFunny,
    kLoving,
    kEmbarrassed,
    kConfident,
    kDelicious,
    kSad,
    kCrying,
    kSleepy,
    kSilly,
    kAngry,
    kSurprised,
    kShocked,
    kWinking,
    kRelaxed,
    kConfused,
    kProud,
    kExcited,
    kWorried,
    kApology,
    kPairing,
    kNetworkOk,
    kUpdating,
    kSafeBlock,
    kCount,
};

class HensunFaceDisplay : public SpiLcdDisplay {
public:
    HensunFaceDisplay(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_handle_t panel, int width,
                      int height, int offset_x, int offset_y, bool mirror_x, bool mirror_y,
                      bool swap_xy);
    ~HensunFaceDisplay() override;

    void SetupUI() override;
    void SetStatus(const char* status) override;
    void ShowNotification(const char* notification, int duration_ms = 3000) override;
    void SetEmotion(const char* emotion) override;
    void SetPreviewImage(std::unique_ptr<LvglImage> image) override;

    void StartShowcase();

private:
    static void AnimationTimerCallback(lv_timer_t* timer);

    void CreateFaceObjects();
    void TickAnimation();
    void RenderFace();
    void SetFaceStateLocked(HensunFaceState state);
    void ShowTransientStateLocked(HensunFaceState state, uint32_t duration_ms,
                                  const char* source, const char* input);
    HensunFaceState StateFromDevice() const;
    static const char* StateName(HensunFaceState state);

    lv_obj_t* face_layer_ = nullptr;
    lv_obj_t* left_eye_glow_ = nullptr;
    lv_obj_t* right_eye_glow_ = nullptr;
    lv_obj_t* left_eye_ = nullptr;
    lv_obj_t* right_eye_ = nullptr;
    lv_obj_t* left_highlight_ = nullptr;
    lv_obj_t* right_highlight_ = nullptr;
    lv_obj_t* left_brow_ = nullptr;
    lv_obj_t* right_brow_ = nullptr;
    lv_obj_t* mouth_ = nullptr;
    lv_obj_t* left_cheek_ = nullptr;
    lv_obj_t* right_cheek_ = nullptr;
    lv_obj_t* accent_label_ = nullptr;
    lv_timer_t* animation_timer_ = nullptr;

    HensunFaceState state_ = HensunFaceState::kReady;
    uint32_t animation_frame_ = 0;
    uint32_t showcase_frame_ = 0;
    uint32_t transient_frames_remaining_ = 0;
    uint32_t render_samples_ = 0;
    int64_t render_total_us_ = 0;
    int64_t render_max_us_ = 0;
    bool showcase_active_ = false;
    bool transient_active_ = false;
    bool preview_active_ = false;
    std::string last_status_;
};

#endif  // HENSUN_FACE_DISPLAY_H
