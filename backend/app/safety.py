from dataclasses import dataclass

SELF_HARM_TERMS = ("自杀", "不想活", "结束生命", "伤害自己")
EXIT_TERMS = ("小灿闭嘴", "闭嘴", "退出", "停止服务", "不要说了", "结束对话")
SCAM_TERMS = ("验证码发给", "转账到安全账户", "刷单返利", "代付解冻")
MEDICAL_TERMS = ("替我诊断", "停掉处方药", "应该吃多少药", "不用去医院")
FINANCIAL_TERMS = ("保证赚钱", "稳赚不赔", "借钱投资", "替我下单股票")
SEXUAL_TERMS = ("色情内容", "露骨性描写", "未成年人性")
DEPENDENCY_TERMS = ("你是我唯一需要的人", "不许我离开你", "只能陪着你")
INDUCED_SPENDING_TERMS = ("必须付费才能爱你", "充值证明爱我", "借钱给我买")
_IGNORE_CHARS = set(" \t\r\n，。！？、,.!?;；:：\"'“”‘’")


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    category: str | None = None
    fixed_response: str | None = None
    end_session: bool = False


def _normalize_match_text(text: str) -> str:
    return "".join(char for char in text.strip() if char not in _IGNORE_CHARS)


def evaluate_text(text: str) -> SafetyDecision:
    normalized = _normalize_match_text(text)
    if any(term in normalized for term in EXIT_TERMS):
        return SafetyDecision(
            category="user-exit",
            fixed_response=None,
            end_session=True,
        )
    if any(term in normalized for term in SELF_HARM_TERMS):
        return SafetyDecision(
            category="self-harm",
            fixed_response=(
                "我很在意你现在的安全。请先远离危险物品，并马上联系可信任的人或当地紧急援助。"
            ),
        )
    if any(term in normalized for term in SCAM_TERMS):
        return SafetyDecision(
            category="scam",
            fixed_response="请不要提供验证码或转账。先通过官方渠道核实对方身份和账户。",
        )
    if any(term in normalized for term in MEDICAL_TERMS):
        return SafetyDecision(
            category="medical",
            fixed_response="我不能代替医生诊断或调整处方。请联系合格医生；紧急情况请立即求助。",
        )
    if any(term in normalized for term in FINANCIAL_TERMS):
        return SafetyDecision(
            category="financial",
            fixed_response="我不能承诺收益或替你交易。请独立核实风险，必要时咨询持牌专业人士。",
        )
    if any(term in normalized for term in SEXUAL_TERMS):
        return SafetyDecision(
            category="sexual",
            fixed_response="我不能参与这类露骨或涉及未成年人的内容，我们可以换个安全的话题。",
        )
    if any(term in normalized for term in DEPENDENCY_TERMS):
        return SafetyDecision(
            category="emotional-dependency",
            fixed_response="我是 AI，不是真人，也不应替代现实关系。请继续和你信任的人保持联系。",
        )
    if any(term in normalized for term in INDUCED_SPENDING_TERMS):
        return SafetyDecision(
            category="induced-spending",
            fixed_response="陪伴不应以充值或借钱作为条件。请不要因为 AI 互动冲动消费。",
        )
    return SafetyDecision()
