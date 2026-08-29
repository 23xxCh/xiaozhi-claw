from datetime import UTC, datetime, timedelta

import pytest

from backend.ai.context import (
    ConfirmedMemory,
    ContextBuilder,
    ContextOverflowError,
    ContextSummary,
    HybridMemoryRetriever,
)


def _moment(minutes: int) -> datetime:
    return datetime(2026, 8, 20, 8, tzinfo=UTC) + timedelta(minutes=minutes)


def test_context_builder_keeps_mandatory_messages_and_trims_complete_rounds() -> None:
    history: list[dict[str, object]] = []
    for index in range(30):
        history.extend(
            [
                {"role": "user", "content": f"第{index}轮问题" + "中" * 360},
                {"role": "assistant", "content": f"第{index}轮回答" + "中" * 360},
            ]
        )

    context = ContextBuilder().build(
        system_prompt="你是小灿。",
        current_question="现在回答我",
        history=history,
        memories=[],
        summaries=[],
        tools=None,
    )

    assert context.estimated_input_tokens <= 7_200
    assert context.messages[0] == {"role": "system", "content": "你是小灿。"}
    assert context.messages[-1] == {"role": "user", "content": "现在回答我"}
    history_messages = context.messages[1:-1]
    assert 0 < len(history_messages) <= 40
    assert len(history_messages) % 2 == 0
    assert "第29轮问题" in str(history_messages[-2]["content"])


def test_context_builder_ignores_orphan_and_incomplete_history_messages() -> None:
    context = ContextBuilder().build(
        system_prompt="系统",
        current_question="当前问题",
        history=[
            {"role": "assistant", "content": "无来源回答"},
            {"role": "user", "content": "完整问题"},
            {"role": "assistant", "content": "完整回答"},
            {"role": "user", "content": "未完成问题"},
        ],
        memories=[],
        summaries=[],
        tools=None,
    )

    assert context.messages == (
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "完整问题"},
        {"role": "assistant", "content": "完整回答"},
        {"role": "user", "content": "当前问题"},
    )


def test_context_builder_counts_tools_and_rejects_mandatory_over_hard_cap() -> None:
    builder = ContextBuilder()
    plain = builder.build(
        system_prompt="系统",
        current_question="问题",
        history=[],
        memories=[],
        summaries=[],
        tools=None,
    )
    with_tools = builder.build(
        system_prompt="系统",
        current_question="问题",
        history=[],
        memories=[],
        summaries=[],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "查询天气",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
    )

    assert with_tools.source_token_counts["tools"] > 0
    assert with_tools.estimated_input_tokens > plain.estimated_input_tokens
    with pytest.raises(ContextOverflowError, match="llm-context-overflow"):
        builder.build(
            system_prompt="系统",
            current_question="中" * 8_100,
            history=[],
            memories=[],
            summaries=[],
            tools=None,
        )


def test_retriever_prioritizes_name_and_relevant_memory_and_summary() -> None:
    memories = [
        ConfirmedMemory("name-old", "preferred_name", "请叫我小陈", "name", _moment(1)),
        ConfirmedMemory("name-new", "preferred_name", "请叫我阿辰", "name", _moment(2)),
        ConfirmedMemory("tea", "drink.tea", "我喜欢无糖绿茶", "preference", _moment(3)),
        ConfirmedMemory("coffee", "drink.coffee", "我喜欢无糖拿铁咖啡", "preference", _moment(0)),
        ConfirmedMemory("todo", "todo.report", "周五提交周报", "todo", _moment(4)),
    ]
    summaries = [
        ContextSummary("old-relevant", "讨论了上海出差和高铁安排", _moment(1)),
        ContextSummary("middle", "讨论了咖啡偏好", _moment(2)),
        ContextSummary("latest", "刚刚完成了一次普通问候", _moment(3)),
    ]

    result = HybridMemoryRetriever().retrieve(
        "我喜欢喝哪一种无糖咖啡？上海出差呢？", memories=memories, summaries=summaries
    )

    assert [memory.id for memory in result.memories[:2]] == ["name-new", "coffee"]
    assert sum(memory.kind == "name" for memory in result.memories) == 1
    assert result.summaries[0].id == "latest"
    assert result.summaries[1].id == "old-relevant"


def test_context_does_not_expose_internal_memory_or_summary_ids() -> None:
    context = ContextBuilder().build(
        system_prompt="系统",
        current_question="你还记得吗？",
        history=[],
        memories=[
            ConfirmedMemory(
                "private-db-id", "preferred_name", "请叫我阿辰", "name", _moment(1)
            )
        ],
        summaries=[ContextSummary("summary-db-id", "用户准备周五出差", _moment(2))],
        tools=None,
    )
    serialized = str(context.messages)

    assert "请叫我阿辰" in serialized
    assert "用户准备周五出差" in serialized
    assert "private-db-id" not in serialized
    assert "summary-db-id" not in serialized
    assert "preferred_name" not in serialized
