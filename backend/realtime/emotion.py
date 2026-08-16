from dataclasses import dataclass


@dataclass(frozen=True)
class EmotionDecision:
    user_emotion: str
    thinking_emotion: str
    reply_emotion: str


class EmotionRouter:
    """Maps ASR user emotion and safety state to the stable Hensun face contract."""

    _valid_user_emotions = {
        "surprised",
        "neutral",
        "happy",
        "sad",
        "disgusted",
        "angry",
        "fearful",
    }

    def route(self, user_emotion: str | None, safety_category: str | None) -> EmotionDecision:
        normalized = user_emotion if user_emotion in self._valid_user_emotions else "neutral"
        if safety_category is not None and safety_category not in {"user-exit"}:
            return EmotionDecision(normalized, "safe_block", "safe_block")
        if safety_category == "user-exit":
            return EmotionDecision(normalized, "apology", "apology")
        if normalized in {"sad", "fearful"}:
            return EmotionDecision(normalized, "caring", "caring")
        if normalized == "happy":
            return EmotionDecision(normalized, "curious", "happy")
        if normalized == "surprised":
            return EmotionDecision(normalized, "curious", "surprised")
        if normalized in {"angry", "disgusted"}:
            return EmotionDecision(normalized, "worried", "relaxed")
        return EmotionDecision(normalized, "thinking", "happy")
