#pragma once
// Generated device WSS V1 vocabulary. Do not edit by hand.
#include <array>
#include <string_view>

namespace hensun::device_ws {
constexpr int kProtocolVersion = 1;
constexpr std::array<std::string_view, 7> kMessageTypes = {{"hello", "listen", "tts", "mcp", "system", "alert", "device_config_ack"}};
constexpr std::array<std::string_view, 4> kTtsStates = {{"start", "ready", "stop", "drained"}};
constexpr std::array<std::string_view, 3> kOptionalCorrelationFields = {{"session_id", "turn_id", "reply_id"}};
}  // namespace hensun::device_ws
