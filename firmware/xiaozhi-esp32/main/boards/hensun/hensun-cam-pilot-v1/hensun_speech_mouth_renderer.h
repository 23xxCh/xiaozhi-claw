#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

class HensunSpeechMouthRenderer final {
public:
    static constexpr uint8_t kPoseCount = 5;
    static constexpr int kMouthCenterX = 160;
    static constexpr int kMouthCenterY = 170;

    HensunSpeechMouthRenderer() = default;
    ~HensunSpeechMouthRenderer();

    HensunSpeechMouthRenderer(const HensunSpeechMouthRenderer&) = delete;
    HensunSpeechMouthRenderer& operator=(const HensunSpeechMouthRenderer&) = delete;

    bool Initialize(int display_width, int max_stripe_height);
    void SetActive(bool active);
    void SetLevel(uint8_t pose);
    const void* ComposeStripe(const void* source, int x_start, int y_start,
                              int x_end, int y_end);

private:
    static constexpr int64_t kSilenceCloseDelayUs = 300 * 1000;
    static constexpr int64_t kPoseStepIntervalUs = 50 * 1000;
    static constexpr int kSourceMouthLeft = 140;
    static constexpr int kSourceMouthTop = 154;
    static constexpr int kSourceMouthRight = 180;
    static constexpr int kSourceMouthBottom = 176;
    static constexpr uint16_t kFaceWhiteRgb565 = 0xf7be;
    static constexpr uint16_t kFaceBlackRgb565 = 0x0000;

    static void EraseSourceMouthPixels(uint16_t* pixels, int stripe_width,
                                      int stripe_height, int x_start, int y_start);
    void DrawPose(uint16_t* pixels, int stripe_width, int stripe_height,
                  int x_start, int y_start, uint8_t pose) const;
    static void Plot(uint16_t* pixels, int stripe_width, int stripe_height,
                     int x_start, int y_start, int x, int y);
    static void DrawSmileArc(uint16_t* pixels, int stripe_width, int stripe_height,
                             int x_start, int y_start, int half_width, int depth,
                             int thickness);

    uint16_t* buffers_[2] = {};
    size_t buffer_pixels_ = 0;
    uint8_t next_buffer_ = 0;
    std::atomic<bool> active_{false};
    std::atomic<uint8_t> target_pose_{0};
    std::atomic<uint8_t> render_pose_{0};
    std::atomic<int64_t> last_non_silent_us_{0};
    std::atomic<int64_t> last_pose_step_us_{0};
};
