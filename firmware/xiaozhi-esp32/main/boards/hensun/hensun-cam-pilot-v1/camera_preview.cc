#include "camera_preview.h"

#include "emote_renderer.h"
#include "hensun_panel.h"

#include <cstring>

#include <esp_heap_caps.h>
#include <esp_log.h>

namespace {
constexpr char kTag[] = "CameraPreview";
}

CameraPreview::CameraPreview(HensunPanel& panel, EmoteRenderer& renderer,
                             int width, int height)
    : panel_(panel), renderer_(renderer), width_(width), height_(height) {}

bool CameraPreview::Show(const uint16_t* pixels, size_t pixel_count,
                         int width, int height, int stride_bytes,
                         int duration_ms) {
    if (!renderer_.ready() || pixels == nullptr || width <= 0 || height <= 0 ||
        stride_bytes < width * static_cast<int>(sizeof(uint16_t)) ||
        pixel_count < static_cast<size_t>(width) * height) {
        return false;
    }
    const size_t output_pixels = static_cast<size_t>(width_) * height_;
    auto* frame = static_cast<uint16_t*>(heap_caps_malloc(
        output_pixels * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (frame == nullptr) {
        ESP_LOGE(kTag, "preview allocation failed");
        return false;
    }

    const int source_stride_pixels =
        stride_bytes / static_cast<int>(sizeof(uint16_t));
    if (width == width_ && height == height_) {
        for (int row = 0; row < height_; ++row) {
            std::memcpy(frame + static_cast<size_t>(row) * width_,
                        pixels + static_cast<size_t>(row) * source_stride_pixels,
                        static_cast<size_t>(width_) * sizeof(uint16_t));
        }
    } else if (width == height_ && height == width_) {
        RotateRgb565Clockwise(
            pixels, width, height, source_stride_pixels, frame);
    } else {
        ESP_LOGW(kTag, "unsupported preview frame: %dx%d", width, height);
        heap_caps_free(frame);
        return false;
    }

    if (!renderer_.Lock()) {
        heap_caps_free(frame);
        return false;
    }
    panel_.WaitForAnimationFlushes(250);
    const bool displayed = panel_.DrawPreview(frame, duration_ms);
    heap_caps_free(frame);
    renderer_.Unlock();
    return displayed;
}

void CameraPreview::RotateRgb565Clockwise(
    const uint16_t* source, int source_width, int source_height,
    int source_stride_pixels, uint16_t* destination) {
    for (int source_y = 0; source_y < source_height; ++source_y) {
        for (int source_x = 0; source_x < source_width; ++source_x) {
            const int destination_x = source_height - 1 - source_y;
            const int destination_y = source_x;
            destination[static_cast<size_t>(destination_y) * source_height +
                        destination_x] =
                source[static_cast<size_t>(source_y) * source_stride_pixels +
                       source_x];
        }
    }
}
