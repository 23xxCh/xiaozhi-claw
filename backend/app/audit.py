import json

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent


def add_audit_event(
    session: AsyncSession,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    payload: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            payload_json=json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),
        )
    )
