import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from backend.app.claims import _is_claim_key_collision, begin_claim_issuance, issue_claim
from backend.app.config import Settings
from backend.app.models import Device


def _sqlite_error(code: int, message: str) -> IntegrityError:
    original = sqlite3.IntegrityError(message)
    original.sqlite_errorcode = code
    return IntegrityError("INSERT INTO claims", {}, original)


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            IntegrityError(
                "INSERT INTO claims", {}, Exception(1062, "Duplicate entry for PRIMARY")
            ),
            True,
        ),
        (
            IntegrityError(
                "INSERT INTO claims", {}, Exception(1062, "Duplicate entry for code_hash")
            ),
            True,
        ),
        (IntegrityError("INSERT INTO claims", {}, Exception(1452, "Foreign key violation")), False),
        (IntegrityError("INSERT INTO claims", {}, Exception(1048, "Column cannot be null")), False),
        (
            _sqlite_error(
                sqlite3.SQLITE_CONSTRAINT_UNIQUE, "UNIQUE constraint failed: claims.code_hash"
            ),
            True,
        ),
        (
            _sqlite_error(
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY, "UNIQUE constraint failed: claims.id"
            ),
            True,
        ),
        (
            _sqlite_error(
                sqlite3.SQLITE_CONSTRAINT_UNIQUE, "UNIQUE constraint failed: devices.serial_number"
            ),
            False,
        ),
        (
            _sqlite_error(sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY, "FOREIGN KEY constraint failed"),
            False,
        ),
        (
            _sqlite_error(
                sqlite3.SQLITE_CONSTRAINT_NOTNULL, "NOT NULL constraint failed: claims.device_id"
            ),
            False,
        ),
        (
            _sqlite_error(
                sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY, "UNIQUE constraint failed: claims.code_hash"
            ),
            False,
        ),
    ],
)
def test_only_duplicate_claim_keys_are_retryable(error: IntegrityError, expected: bool) -> None:
    assert _is_claim_key_collision(error) is expected


@asynccontextmanager
async def _savepoint():
    yield


@pytest.mark.asyncio
async def test_mysql_collision_retries_without_reading_or_locking_foreign_claim() -> None:
    collision = IntegrityError("INSERT INTO claims", {}, Exception(1062, "Duplicate entry"))
    session = Mock()
    session.scalars = AsyncMock(return_value=[])
    session.scalar = AsyncMock(side_effect=AssertionError("must not query the collision row"))
    session.begin_nested = Mock(side_effect=_savepoint)
    session.flush = AsyncMock(side_effect=[collision, None])
    settings = Settings(device_credential_pepper="test-pepper")
    code, claim = await issue_claim(session, Device(id="test-device"), settings, datetime.now(UTC))
    inserted = [call.args[0] for call in session.add.call_args_list]
    assert len(inserted) == 2
    assert inserted[0].id != inserted[1].id
    assert claim is inserted[1]
    assert len(code) == 6 and code.isdigit()
    session.scalar.assert_not_called()
    assert session.scalars.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        IntegrityError("INSERT INTO claims", {}, Exception(1452, "Foreign key violation")),
        OperationalError("INSERT INTO claims", {}, Exception(1213, "Deadlock found")),
    ],
)
async def test_other_constraint_errors_and_deadlocks_escape_savepoint_retry(failure) -> None:
    session = Mock()
    session.scalars = AsyncMock(return_value=[])
    session.begin_nested = Mock(side_effect=_savepoint)
    session.flush = AsyncMock(side_effect=failure)
    with pytest.raises(type(failure)) as raised:
        await issue_claim(session, Device(id="test-device"), Settings(), datetime.now(UTC))
    assert raised.value is failure
    assert session.flush.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["mysql+asyncmy://", "sqlite+aiosqlite://"])
async def test_issuance_isolation_uses_real_dialect_without_opening_database(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with AsyncSession(engine) as session:
            session.connection = AsyncMock()
            await begin_claim_issuance(session)
            if engine.dialect.name == "mysql":
                session.connection.assert_awaited_once_with(
                    execution_options={"isolation_level": "READ COMMITTED"}
                )
            else:
                session.connection.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mysql_isolation_refuses_an_already_started_session_transaction() -> None:
    engine = create_async_engine("mysql+asyncmy://")
    try:
        async with AsyncSession(engine) as session:
            session.connection = AsyncMock()
            await session.begin()  # Virtual transaction only; no connection is opened.
            with pytest.raises(RuntimeError, match="before database use"):
                await begin_claim_issuance(session)
            session.connection.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("endpoint", ["bootstrap", "xiaozhi-bootstrap"])
def test_both_bootstraps_configure_isolation_before_first_query(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    from backend.app.routers import devices, xiaozhi_bootstrap

    registered = client.post(
        "/v1/admin/devices",
        headers=admin_headers,
        json={"serial_number": "HENSUN-ISOLATION", "board_type": "hensun-nocam-pilot-v1"},
    )
    assert registered.status_code == 200, registered.text
    calls = []

    async def prepare(session: AsyncSession) -> None:
        assert not session.in_transaction(), "bootstrap queried before selecting isolation"
        calls.append(session)
        await begin_claim_issuance(session)

    module = devices if endpoint == "bootstrap" else xiaozhi_bootstrap
    monkeypatch.setattr(module, "begin_claim_issuance", prepare)
    response = client.post(
        f"/v1/device/{endpoint}",
        headers={
            "Device-Id": "HENSUN-ISOLATION",
            "Authorization": f"Bearer {registered.json()['device_secret']}",
        },
        json={"firmware_version": "2.4.2", "application": {"version": "2.4.2"}},
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
