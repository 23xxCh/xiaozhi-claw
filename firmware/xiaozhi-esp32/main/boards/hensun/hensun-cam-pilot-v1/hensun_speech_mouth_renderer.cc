#include "hensun_speech_mouth_renderer.h"

#include <algorithm>
#include <cstring>

#include <esp_heap_caps.h>
#include <esp_timer.h>

HensunSpeechMouthRenderer::~HensunSpeechMouthRenderer() {
    for (auto*& buffer : buffers_) {
        if (buffer != nullptr) {
            heap_caps_free(buffer);
            buffer = nullptr;
        }
    }
}

bool HensunSpeechMouthRenderer::Initialize(int display_width, int max_stripe_height) {
    if (display_width <= 0 || max_stripe_height <= 0) {
        return false;
    }
    buffer_pixels_ = static_cast<size_t>(display_width) * max_stripe_height;
    for (auto*& buffer : buffers_) {
        buffer = static_cast<uint16_t*>(heap_caps_calloc(
            buffer_pixels_, sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
        if (buffer == nullptr) {
            return false;
        }
    }
    return true;
}

void HensunSpeechMouthRenderer::SetActive(bool active) {
    active_.store(active);
    target_pose_.store(0);
    render_pose_.store(0);
    const int64_t now_us = active ? esp_timer_get_time() : 0;
    last_non_silent_us_.store(now_us);
    last_pose_step_us_.store(now_us);
}

void HensunSpeechMouthRenderer::SetLevel(uint8_t pose) {
    if (!active_.load()) {
        return;
    }
    pose = std::min<uint8_t>(pose, kPoseCount - 1);
    const int64_t now_us = esp_timer_get_time();
    if (pose > 0) {
        last_non_silent_us_.store(now_us);
        target_pose_.store(pose);
        return;
    }
    if (now_us - last_non_silent_us_.load() >= kSilenceCloseDelayUs) {
        target_pose_.store(0);
    }
}

const void* HensunSpeechMouthRenderer::ComposeStripe(
        const void* source, int x_start, int y_start, int x_end, int y_end) {
    if (!active_.load() || source == nullptr || x_end <= x_start || y_end <= y_start) {
        return source;
    }
    const int stripe_width = x_end - x_start;
    const int stripe_height = y_end - y_start;
    const size_t pixel_count = static_cast<size_t>(stripe_width) * stripe_height;
    if (pixel_count > buffer_pixels_ || buffers_[next_buffer_] == nullptr) {
        return source;
    }
    uint16_t* destination = buffers_[next_buffer_];
    next_buffer_ ^= 1;
    std::memcpy(destination, source, pixel_count * sizeof(uint16_t));
    if (y_start == 0) {
        const int64_t now_us = esp_timer_get_time();
        const int64_t last_step_us = last_pose_step_us_.load();
        if (now_us - last_step_us >= kPoseStepIntervalUs) {
            uint8_t pose = render_pose_.load();
            const uint8_t target = target_pose_.load();
            if (pose < target) {
                ++pose;
            } else if (pose > target) {
                --pose;
            }
            render_pose_.store(pose);
            last_pose_step_us_.store(now_us);
        }
    }
    EraseSourceMouthPixels(destination, stripe_width, stripe_height, x_start, y_start);
    DrawPose(destination, stripe_width, stripe_height, x_start, y_start,
             render_pose_.load());
    return destination;
}

void HensunSpeechMouthRenderer::EraseSourceMouthPixels(
        uint16_t* pixels, int stripe_width, int stripe_height,
        int x_start, int y_start) {
    const int left = std::max(x_start, kSourceMouthLeft);
    const int top = std::max(y_start, kSourceMouthTop);
    const int right = std::min(x_start + stripe_width, kSourceMouthRight);
    const int bottom = std::min(y_start + stripe_height, kSourceMouthBottom);
    for (int y = top; y < bottom; ++y) {
        for (int x = left; x < right; ++x) {
            auto& pixel = pixels[static_cast<size_t>(y - y_start) * stripe_width +
                                 (x - x_start)];
            const int red = (pixel >> 11) & 0x1f;
            const int green = (pixel >> 5) & 0x3f;
            const int blue = pixel & 0x1f;
            if (red * 2 + green + blue * 2 >= 20) {
                pixel = kFaceBlackRgb565;
            }
        }
    }
}

void HensunSpeechMouthRenderer::DrawPose(
        uint16_t* pixels, int stripe_width, int stripe_height,
        int x_start, int y_start, uint8_t pose) const {
    switch (pose) {
        case 0:
            DrawSmileArc(pixels, stripe_width, stripe_height, x_start, y_start, 10, 2, 2);
            break;
        case 1:
            DrawSmileArc(pixels, stripe_width, stripe_height, x_start, y_start, 12, 3, 2);
            break;
        case 2:
            DrawSmileArc(pixels, stripe_width, stripe_height, x_start, y_start, 14, 5, 3);
            break;
        case 3:
            DrawSmileArc(pixels, stripe_width, stripe_height, x_start, y_start, 16, 7, 3);
            break;
        default:
            DrawSmileArc(pixels, stripe_width, stripe_height, x_start, y_start, 18, 9, 4);
            break;
    }
}

void HensunSpeechMouthRenderer::Plot(
        uint16_t* pixels, int stripe_width, int stripe_height,
        int x_start, int y_start, int x, int y) {
    const int local_x = x - x_start;
    const int local_y = y - y_start;
    if (local_x < 0 || local_x >= stripe_width || local_y < 0 || local_y >= stripe_height) {
        return;
    }
    pixels[static_cast<size_t>(local_y) * stripe_width + local_x] = kFaceWhiteRgb565;
}

void HensunSpeechMouthRenderer::DrawSmileArc(
        uint16_t* pixels, int stripe_width, int stripe_height,
        int x_start, int y_start, int half_width, int depth, int thickness) {
    const int divisor = std::max(1, half_width * half_width);
    for (int x = -half_width; x <= half_width; ++x) {
        const int y = kMouthCenterY + depth - (depth * x * x) / divisor;
        for (int offset = 0; offset < thickness; ++offset) {
            Plot(pixels, stripe_width, stripe_height, x_start, y_start,
                 kMouthCenterX + x, y + offset);
        }
    }
}
