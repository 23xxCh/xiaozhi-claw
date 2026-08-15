#include "speech_envelope.h"

#include <algorithm>

#include <esp_timer.h>

namespace {
constexpr const char* kSpeakingAnimations[4] = {
    "speaking_0", "speaking_1", "speaking", "speaking_3",
};
}

SpeechEnvelope::SpeechEnvelope(SpeechEnvelopeParameters parameters)
    : parameters_(parameters) {}

void SpeechEnvelope::SetSpeaking(bool speaking) {
    speaking_.store(speaking);
    if (speaking) {
        level_.store(2);
        last_switch_ms_.store(0);
    }
}

void SpeechEnvelope::SetParameters(SpeechEnvelopeParameters parameters) {
    parameters_ = parameters;
}

const char* SpeechEnvelope::UpdatePcm(const std::vector<int16_t>& pcm) {
    return UpdateLevel(
        MeasureLevel(pcm, parameters_), esp_timer_get_time() / 1000);
}

const char* SpeechEnvelope::UpdateLevel(uint8_t level, int64_t now_ms) {
    if (!speaking_.load()) {
        return nullptr;
    }
    const uint8_t current = level_.load();
    const uint8_t next = Quantize(level, current);
    if (next == current ||
        now_ms - last_switch_ms_.load() < parameters_.switch_min_interval_ms) {
        return nullptr;
    }
    level_.store(next);
    last_switch_ms_.store(now_ms);
    return kSpeakingAnimations[next];
}

uint8_t SpeechEnvelope::MeasureLevel(
    const std::vector<int16_t>& pcm,
    const SpeechEnvelopeParameters& parameters) {
    if (pcm.empty() || parameters.sample_stride == 0 ||
        parameters.reference_amplitude <= parameters.noise_floor) {
        return 0;
    }
    uint64_t amplitude_sum = 0;
    size_t sample_count = 0;
    for (size_t index = 0; index < pcm.size(); index += parameters.sample_stride) {
        const int32_t sample = pcm[index];
        amplitude_sum += static_cast<uint32_t>(sample < 0 ? -sample : sample);
        ++sample_count;
    }
    if (sample_count == 0) {
        return 0;
    }
    const uint32_t mean = static_cast<uint32_t>(amplitude_sum / sample_count);
    if (mean <= parameters.noise_floor) {
        return 0;
    }
    const uint32_t scaled =
        (mean - parameters.noise_floor) * 100 /
        (parameters.reference_amplitude - parameters.noise_floor);
    return static_cast<uint8_t>(std::min<uint32_t>(100, scaled));
}

uint8_t SpeechEnvelope::Quantize(uint8_t level, uint8_t current_level) {
    switch (current_level) {
        case 0:
            return level >= 14 ? 1 : 0;
        case 1:
            if (level <= 6) {
                return 0;
            }
            return level >= 38 ? 2 : 1;
        case 2:
            if (level <= 25) {
                return 1;
            }
            return level >= 72 ? 3 : 2;
        default:
            return level <= 58 ? 2 : 3;
    }
}
