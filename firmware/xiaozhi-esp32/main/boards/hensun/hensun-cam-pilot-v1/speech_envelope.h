#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <vector>

struct SpeechEnvelopeParameters {
    uint32_t noise_floor = 180;
    uint32_t reference_amplitude = 5000;
    size_t sample_stride = 8;
    int64_t switch_min_interval_ms = 120;
};

class SpeechEnvelope final {
public:
    explicit SpeechEnvelope(SpeechEnvelopeParameters parameters = {});

    void SetSpeaking(bool speaking);
    void SetParameters(SpeechEnvelopeParameters parameters);
    const char* UpdatePcm(const std::vector<int16_t>& pcm);
    const char* UpdateLevel(uint8_t level, int64_t now_ms);

    static uint8_t MeasureLevel(
        const std::vector<int16_t>& pcm,
        const SpeechEnvelopeParameters& parameters = {});

private:
    static uint8_t Quantize(uint8_t level, uint8_t current_level);

    SpeechEnvelopeParameters parameters_;
    std::atomic<bool> speaking_{false};
    std::atomic<uint8_t> level_{2};
    std::atomic<int64_t> last_switch_ms_{0};
};
