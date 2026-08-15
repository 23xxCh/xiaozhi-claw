import logging

from fastapi import WebSocket

from backend.app.models import EncryptedSessionSummary
from backend.app.security import encrypt_memory

from .providers import llm_for
from .snapshot import AgentSnapshot

logger = logging.getLogger(__name__)


async def save_session_summary(
    websocket: WebSocket,
    conversation_id: str,
    user_id: str,
    snapshot: AgentSnapshot,
    history: list[dict[str, str]],
) -> None:
    if not snapshot.memory_consent or not history:
        return
    prompt = "请把这次对话概括为不超过120字的偏好和待办摘要，不要记录敏感原文。"
    parts: list[str] = []
    try:
        async for token in llm_for(
            websocket.app.state.realtime_providers, snapshot.llm_provider
        ).reply_stream(
            prompt,
            history[-20:],
            [],
            system_prompt="只输出简短、客观的会话摘要。",
            model=snapshot.llm_model,
            temperature=0.2,
        ):
            parts.append(token)
        summary = "".join(parts).strip()[:500]
        if not summary:
            return
        async with websocket.app.state.session_factory() as session:
            session.add(
                EncryptedSessionSummary(
                    session_id=conversation_id,
                    user_id=user_id,
                    agent_id=snapshot.agent_id,
                    encrypted_summary=encrypt_memory(summary, websocket.app.state.settings),
                )
            )
            await session.commit()
    except Exception:
        logger.exception("session summary failed for conversation %s", conversation_id)
