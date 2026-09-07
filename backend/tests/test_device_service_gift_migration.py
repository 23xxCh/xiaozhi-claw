import json
import sqlite3
from pathlib import Path

import pytest

from .test_device_config_contract_migration import _alembic


@pytest.mark.parametrize("partial_ddl", [False, True])
def test_service_gift_migration_preserves_legacy_activation_evidence(
    tmp_path: Path, partial_ddl: bool
) -> None:
    database = tmp_path / "service-gifts.db"
    _alembic(database, "upgrade", "20260907_09")
    with sqlite3.connect(database) as connection:
        connection.execute("""
            INSERT INTO users (id, adult_confirmed, created_at)
            VALUES ('legacy-user', 1, '2026-08-01')
        """)
        for device_id, owner, lifecycle, reset_epoch in (
            ("inventory", None, "factory-unclaimed", 0),
            ("owner-evidence", "legacy-user", "factory-unclaimed", 0),
            ("lifecycle-evidence", None, "owned-recovery-required", 0),
            ("reset-evidence", None, "factory-unclaimed", 1),
            ("consumed-code-evidence", None, "factory-unclaimed", 0),
            ("claimed-audit-evidence", None, "factory-unclaimed", 0),
            ("unbound-audit-evidence", None, "factory-unclaimed", 0),
        ):
            connection.execute(
                """
                INSERT INTO devices (
                    id, serial_number, credential_hash, board_type, lifecycle, owner_user_id,
                    memory_consent, firmware_version, reset_epoch, created_at
                ) VALUES (?, ?, 'hash', 'hensun-desk-v1', ?, ?, 0, '2.4.2', ?, '2026-08-01')
            """,
                (device_id, device_id, lifecycle, owner, reset_epoch),
            )
        for device_id, consumed in (("inventory", None), ("consumed-code-evidence", "2026-08-02")):
            connection.execute(
                """
                INSERT INTO claims (id, code_hash, device_id, expires_at, consumed_at, created_at)
                VALUES (?, ?, ?, '2026-08-03', ?, '2026-08-01')
            """,
                (device_id, device_id, device_id, consumed),
            )
        for device_id, action in (
            ("claimed-audit-evidence", "device.claimed"),
            ("unbound-audit-evidence", "device.unbound"),
        ):
            connection.execute(
                """
                INSERT INTO audit_events (
                    id, actor_type, actor_id, action, payload_json, created_at
                )
                VALUES (?, 'user', 'legacy-user', ?, ?, '2026-08-02')
            """,
                (device_id, action, json.dumps({"device_id": device_id})),
            )
        connection.execute("""
            INSERT INTO entitlements (
                id, user_id, plan, monthly_turn_limit, starts_at, expires_at, created_at
            ) VALUES ('existing-service', 'legacy-user', 'paid-standard', 1234,
                      '2026-08-01', '2026-12-01', '2026-08-01')
        """)
        if partial_ddl:
            connection.execute("""
                ALTER TABLE devices ADD COLUMN service_gift_status VARCHAR(24)
                NOT NULL DEFAULT 'eligible'
            """)
    _alembic(database, "upgrade", "head")
    _alembic(database, "check")

    def assert_legacy_state() -> None:
        with sqlite3.connect(database) as connection:
            states = dict(connection.execute("SELECT id, service_gift_status FROM devices"))
            assert states.pop("inventory") == "eligible"
            assert set(states.values()) == {"legacy-consumed"}
            assert connection.execute("""
                SELECT count(*) FROM devices WHERE service_gift_granted_at IS NOT NULL
            """).fetchone() == (0,)
            assert connection.execute("""
                SELECT plan, monthly_turn_limit, starts_at, expires_at FROM entitlements
            """).fetchall() == [("paid-standard", 1234, "2026-08-01", "2026-12-01")]

    assert_legacy_state()
    _alembic(database, "downgrade", "20260907_09")
    _alembic(database, "upgrade", "head")
    assert_legacy_state()
    with sqlite3.connect(database) as connection:
        connection.execute("""
            UPDATE devices SET service_gift_status = 'granted',
                service_gift_granted_at = '2026-09-07' WHERE id = 'inventory'
        """)
    with pytest.raises(AssertionError, match="Cannot downgrade after device service gifts"):
        _alembic(database, "downgrade", "20260907_09")
    with sqlite3.connect(database) as connection:
        assert connection.execute("""
            SELECT service_gift_status FROM devices WHERE id = 'inventory'
        """).fetchone() == ("granted",)
