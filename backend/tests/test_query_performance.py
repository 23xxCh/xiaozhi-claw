import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event, select

from backend.app.models import (
    ConversationSession,
    Device,
    DeviceSession,
    EncryptedSessionSummary,
    User,
)
from backend.app.security import encrypt_memory

from .conftest import provision_owned_device


@contextmanager
def _capture_sql(client: TestClient) -> Iterator[list[str]]:
    statements: list[str] = []

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(" ".join(statement.lower().split()))

    engine = client.app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)


def _queries_for(statements: list[str], table: str, column: str) -> list[str]:
    return [
        statement
        for statement in statements
        if f"from {table}" in statement and f"{table}.{column}" in statement
    ]


def _count_queries_for(statements: list[str], table: str, column: str) -> list[str]:
    return [
        statement for statement in _queries_for(statements, table, column) if "count(" in statement
    ]


def test_agent_list_batches_device_counts(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(client, admin_headers, serial="HENSUN-QUERY-AGENT-01")
    headers = {"Authorization": f"Bearer {owned['user_token']}"}
    for name in ("书房助手", "客厅助手"):
        created = client.post("/v1/agents", headers=headers, json={"name": name})
        assert created.status_code == 200, created.text

    with _capture_sql(client) as statements:
        response = client.get("/v1/agents", headers=headers)

    assert response.status_code == 200, response.text
    assert len(response.json()) == 3
    assert len(_count_queries_for(statements, "devices", "active_agent_id")) == 1


def test_device_list_batches_latest_sessions(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    first = provision_owned_device(
        client,
        admin_headers,
        serial="HENSUN-QUERY-DEVICE-01",
        openid="wx-query-devices",
    )
    for suffix in ("02", "03"):
        provision_owned_device(
            client,
            admin_headers,
            serial=f"HENSUN-QUERY-DEVICE-{suffix}",
            openid="wx-query-devices",
        )
    headers = {"Authorization": f"Bearer {first['user_token']}"}

    async def seed_ordered_sessions() -> None:
        async with client.app.state.session_factory() as session:
            now = datetime.now(UTC)
            session.add_all(
                [
                    DeviceSession(
                        device_id=first["device_id"],
                        gateway_id="query-gateway",
                        connection_id="query-session-old",
                        status="online",
                        connected_at=now - timedelta(days=1),
                        heartbeat_at=now,
                    ),
                    DeviceSession(
                        device_id=first["device_id"],
                        gateway_id="query-gateway",
                        connection_id="query-session-latest",
                        status="offline",
                        connected_at=now,
                        heartbeat_at=now,
                    ),
                ]
            )
            await session.commit()

    asyncio.run(seed_ordered_sessions())

    with _capture_sql(client) as statements:
        response = client.get("/v1/devices", headers=headers)

    assert response.status_code == 200, response.text
    assert len(response.json()) == 3
    first_device = next(item for item in response.json() if item["id"] == first["device_id"])
    assert first_device["online"] is False
    assert len(_queries_for(statements, "device_sessions", "device_id")) == 1


def test_admin_user_list_batches_device_counts(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    for index in range(3):
        provision_owned_device(
            client,
            admin_headers,
            serial=f"HENSUN-QUERY-USER-{index}",
            openid=f"wx-query-user-{index}",
        )
    created = client.post(
        "/v1/admin/staff",
        headers=admin_headers,
        json={"username": "query-support", "display_name": "Query", "role": "support"},
    )
    assert created.status_code == 200, created.text
    login = client.post(
        "/v1/admin/auth/login",
        headers=admin_headers,
        json={"username": "query-support"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    with _capture_sql(client) as statements:
        response = client.get("/v1/admin/users", headers=headers)

    assert response.status_code == 200, response.text
    assert len(response.json()) == 3
    assert len(_count_queries_for(statements, "devices", "owner_user_id")) == 1


def test_conversation_list_limits_summary_query_to_visible_sessions(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    owned = provision_owned_device(
        client,
        admin_headers,
        serial="HENSUN-QUERY-CONVERSATION-01",
        openid="wx-query-conversations",
    )
    headers = {"Authorization": f"Bearer {owned['user_token']}"}
    agents = client.get("/v1/agents", headers=headers)
    assert agents.status_code == 200, agents.text
    agent_id = agents.json()[0]["id"]
    consent = client.patch(f"/v1/agents/{agent_id}", headers=headers, json={"memory_consent": True})
    assert consent.status_code == 200, consent.text

    async def seed_conversation() -> str:
        async with client.app.state.session_factory() as session:
            user = await session.scalar(
                select(User).where(User.wechat_openid == "wx-query-conversations")
            )
            device = await session.get(Device, owned["device_id"])
            assert user is not None
            assert device is not None and device.active_profile_id is not None
            conversation = ConversationSession(
                user_id=user.id,
                agent_id=agent_id,
                device_id=device.id,
                usage_profile_id=device.active_profile_id,
                turn_count=1,
                started_at=datetime.now(UTC),
            )
            session.add(conversation)
            await session.flush()
            session.add(
                EncryptedSessionSummary(
                    session_id=conversation.id,
                    user_id=user.id,
                    agent_id=agent_id,
                    encrypted_summary=encrypt_memory("只读取当前页摘要", client.app.state.settings),
                )
            )
            await session.commit()
            return conversation.id

    conversation_id = asyncio.run(seed_conversation())
    with _capture_sql(client) as statements:
        response = client.get("/v1/conversations", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()[0]["id"] == conversation_id
    assert response.json()[0]["summary"] == "只读取当前页摘要"
    summary_queries = _queries_for(statements, "encrypted_session_summaries", "session_id")
    assert len(summary_queries) == 1
    assert "encrypted_session_summaries.session_id in" in summary_queries[0]
