#pragma once

#include <cstddef>
#include <cstdint>

class EmoteRenderer;
class HensunPanel;

class CameraPreview final {
public:
    CameraPreview(HensunPanel& panel, EmoteRenderer& renderer,
                  int width, int height);

    bool Show(const uint16_t* pixels, size_t pixel_count, int width, int height,
              int stride_bytes, int duration_ms = 1500);

private:
    static void RotateRgb565Clockwise(
        const uint16_t* source, int source_width, int source_height,
        int source_stride_pixels, uint16_t* destination);

    HensunPanel& panel_;
    EmoteRenderer& renderer_;
    int width_ = 0;
    int height_ = 0;
};
