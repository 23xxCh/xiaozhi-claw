import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from backend.app.models import (
    ConversationSession,
    DeviceCommand,
    DeviceSession,
    ProviderUsage,
    UsageEvent,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "20260829_07"
CURRENT_HEAD = "20260907_09"
EXPECTED_INDEXES = {
    "usage_events": {
        "ix_usage_events_user_kind_created_at",
        "ix_usage_events_kind_created_at",
    },
    "conversation_sessions": {"ix_conversation_sessions_user_started_at"},
    "provider_usage": {"ix_provider_usage_created_at_operation"},
    "device_sessions": {
        "ix_device_sessions_device_connected_at",
        "ix_device_sessions_status_heartbeat_at",
    },
    "device_commands": {"ix_device_commands_status_created_at"},
}


def _alembic(database: Path, *args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _index_names(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA index_list({table})")}


def test_hot_query_indexes_are_declared_in_model_metadata() -> None:
    models = {
        "usage_events": UsageEvent,
        "conversation_sessions": ConversationSession,
        "provider_usage": ProviderUsage,
        "device_sessions": DeviceSession,
        "device_commands": DeviceCommand,
    }
    for table, model in models.items():
        assert EXPECTED_INDEXES[table] <= {index.name for index in model.__table__.indexes}


def test_hot_query_index_migration_round_trips_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "hot-query-indexes.db"
    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            CURRENT_HEAD,
        )
        for table, indexes in EXPECTED_INDEXES.items():
            assert indexes <= _index_names(connection, table)

    _alembic(database, "downgrade", PREVIOUS_REVISION)
    with sqlite3.connect(database) as connection:
        for table, indexes in EXPECTED_INDEXES.items():
            assert indexes.isdisjoint(_index_names(connection, table))

    _alembic(database, "upgrade", "head")
    _alembic(database, "check")
