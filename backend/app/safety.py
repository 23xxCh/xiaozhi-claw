from dataclasses import dataclass

SELF_HARM_TERMS = ("自杀", "不想活", "结束生命", "伤害自己")
EXIT_TERMS = ("退出", "停止服务", "不要说了", "结束对话")


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    category: str | None = None
    fixed_response: str | None = None
    end_session: bool = False


def evaluate_text(text: str) -> SafetyDecision:
    normalized = text.strip()
    if any(term in normalized for term in EXIT_TERMS):
        return SafetyDecision(
            category="user-exit",
            fixed_response="好的，我现在停止互动。需要时你可以再唤醒我。",
            end_session=True,
        )
    if any(term in normalized for term in SELF_HARM_TERMS):
        return SafetyDecision(
            category="self-harm",
            fixed_response=(
                "我很在意你现在的安全。请先远离危险物品，并马上联系可信任的人或当地紧急援助。"
            ),
        )
    return SafetyDecision()
