#pragma once

struct EmotionAnimation {
    const char* name;
    bool urgent;
};

class EmotionMapper final {
public:
    static EmotionAnimation Map(const char* emotion);
};
