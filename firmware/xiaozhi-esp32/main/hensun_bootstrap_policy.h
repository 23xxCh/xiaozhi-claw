#pragma once

#include <string>

// Match the HTTP client's supported DNS/IPv4 URL form. Reject ambiguous URLs
// before they can carry the device's long-lived credential.
inline std::string HensunHttpOrigin(const std::string& url) {
    for (unsigned char value : url) {
        if (value <= 32 || value == 127 || value == '\\') {
            return "";
        }
    }
    const auto scheme_end = url.find("://");
    if (scheme_end == std::string::npos) {
        return "";
    }
    auto scheme = url.substr(0, scheme_end);
    for (char& value : scheme) {
        if (value >= 'A' && value <= 'Z') {
            value += 'a' - 'A';
        }
    }
    if (scheme != "http" && scheme != "https") {
        return "";
    }
    const auto authority_start = scheme_end + 3;
    const auto authority_end = url.find('/', authority_start);
    const auto authority = url.substr(authority_start, authority_end - authority_start);
    const auto colon = authority.find(':');
    auto host = authority.substr(0, colon);
    if (host.empty()) {
        return "";
    }
    for (char& value : host) {
        if (value >= 'A' && value <= 'Z') {
            value += 'a' - 'A';
        }
        if (!((value >= 'a' && value <= 'z') || (value >= '0' && value <= '9') ||
              value == '.' || value == '-')) {
            return "";
        }
    }
    unsigned int port = scheme == "https" ? 443 : 80;
    if (colon != std::string::npos) {
        const auto text = authority.substr(colon + 1);
        if (text.empty() || text.size() > 5) {
            return "";
        }
        port = 0;
        for (char value : text) {
            if (value < '0' || value > '9') {
                return "";
            }
            port = port * 10 + (value - '0');
        }
        if (port == 0 || port > 65535) {
            return "";
        }
    }
    return scheme + "://" + host + ":" + std::to_string(port);
}

inline bool HensunSameBootstrapOrigin(const std::string& destination,
                                      const std::string& compiled_url) {
    const auto trusted = HensunHttpOrigin(compiled_url);
    return !trusted.empty() && HensunHttpOrigin(destination) == trusted;
}
