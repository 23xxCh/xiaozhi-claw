from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_HONG_KONG = ZoneInfo("Asia/Hong_Kong")
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_STORY_TERMS = (
    "故事",
    "童话",
    "寓言",
)


@dataclass(frozen=True, slots=True)
class VoiceReplyPolicy:
    max_spoken_chars: int
    max_spoken_segments: int
    context: str


def build_voice_reply_policy(
    transcript: str, *, now: datetime | None = None
) -> VoiceReplyPolicy:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local = current.astimezone(_HONG_KONG)
    trusted_time = (
        f"当前可信香港时间：{local.year}年{local.month}月{local.day}日"
        f" {_WEEKDAYS[local.weekday()]} {local:%H:%M}。"
        "回答日期、星期或时间时必须使用这个值，不得猜测。"
    )
    capability = (
        "你可以讲原创短故事、童话和寓言，也可以回答日常知识问题。"
        "对普通无害请求直接完成，不要无故拒绝。"
    )
    normalized = "".join(transcript.split())
    if "新闻" in normalized:
        reply_rule = (
            "用户正在请求新闻。播报最多三条有来源日期的简短新闻，"
            "每条一句完整的话，总长度不超过220个汉字。"
            "查询不到最新消息时明确说明，不要编造。"
        )
        return VoiceReplyPolicy(240, 3, "\n".join((trusted_time, capability, reply_rule)))
    story_requested = any(term in normalized for term in _STORY_TERMS)
    if story_requested:
        reply_rule = (
            "用户正在请求故事。直接开始讲故事，给出有开头、变化和结尾的"
            "完整原创短故事，四到五句，总长度不超过160个汉字。"
        )
        return VoiceReplyPolicy(160, 5, "\n".join((trusted_time, capability, reply_rule)))
    reply_rule = "使用自然口语，只回答一到两句，总长度不超过60个汉字。"
    return VoiceReplyPolicy(60, 2, "\n".join((trusted_time, capability, reply_rule)))
