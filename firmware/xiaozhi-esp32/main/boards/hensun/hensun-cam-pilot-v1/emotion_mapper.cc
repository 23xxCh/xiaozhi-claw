#include "emotion_mapper.h"

#include <cstring>
#include <initializer_list>

#include <esp_log.h>

namespace {
constexpr char kTag[] = "EmotionMapper";

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

EmotionAnimation EmotionMapper::Map(const char* emotion) {
    if (IsOneOf(emotion, {"neutral", "idle", "idle_entered"})) {
        return {"idle", false};
    }
    if (IsOneOf(
            emotion, {"listening", "listening_started", "wake_word_detected"})) {
        return {"listening", false};
    }
    if (IsOneOf(emotion, {"thinking", "processing_started", "clarification",
                          "clarification_needed"})) {
        return {"thinking", false};
    }
    if (IsOneOf(emotion, {"speaking", "query_result_ready"})) {
        return {"speaking", false};
    }
    if (IsOneOf(emotion, {"happy", "positive_response", "laughter", "laughing",
                          "amused", "surprised"})) {
        return {"happy", false};
    }
    if (IsOneOf(
            emotion, {"caring", "comfort_mode_entered", "sad", "fearful", "fear"})) {
        return {"caring", false};
    }
    if (IsOneOf(emotion, {"safe_block", "content_safety_blocked",
                          "network_unavailable", "interrupted"})) {
        return {"caring", true};
    }
    ESP_LOGW(kTag, "unknown emotion: %s", emotion != nullptr ? emotion : "(null)");
    return {"idle", false};
}
