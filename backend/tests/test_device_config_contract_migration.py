import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_REVISION = "20260813_05"
CONTRACT_REVISION = "20260829_07"
CURRENT_HEAD = "20260907_10"


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


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _alembic_check(database: Path) -> None:
    _alembic(database, "check")


def test_device_config_contract_migration_backfills_and_round_trips_sqlite(
    tmp_path: Path,
) -> None:
    database = tmp_path / "device-config-contract.db"
    _alembic(database, "upgrade", PREVIOUS_REVISION)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO devices (
                id, serial_number, credential_hash, board_type, lifecycle,
                memory_consent, firmware_version, reset_epoch, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "device-1",
                "HENSUN-MIGRATION-1",
                "hash",
                "hensun-cam-pilot-v1",
                "factory",
                0,
                "0.1.0",
                0,
                "2026-08-28 00:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO device_configurations (
                device_id, desired_version, applied_version,
                speaker_volume, screen_brightness,
                applied_speaker_volume, applied_screen_brightness,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "device-1",
                3,
                2,
                68,
                74,
                66,
                72,
                "2026-08-28 00:00:00",
            ),
        )
        connection.commit()

    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            CURRENT_HEAD,
        )
        assert {
            "hardware_profile_id",
            "display_profile_id",
            "profile_schema_version",
            "profile_sha256",
            "device_config_schema_version",
        } <= _columns(connection, "devices")
        assert {"schema_version", "desired_values", "applied_values"} <= _columns(
            connection, "device_configurations"
        )
        schema_version, desired_raw, applied_raw = connection.execute(
            """
            SELECT schema_version, desired_values, applied_values
            FROM device_configurations WHERE device_id = ?
            """,
            ("device-1",),
        ).fetchone()
        assert schema_version == 1
        desired = json.loads(desired_raw)
        assert desired["audio.speaker_volume"] == 68
        assert desired["display.brightness"] == 74
        assert json.loads(applied_raw) == {
            "audio.speaker_volume": 66,
            "display.brightness": 72,
        }
    _alembic_check(database)

    _alembic(database, "downgrade", PREVIOUS_REVISION)
    with sqlite3.connect(database) as connection:
        assert {"schema_version", "desired_values", "applied_values"}.isdisjoint(
            _columns(connection, "device_configurations")
        )
        assert connection.execute(
            """
            SELECT desired_version, applied_version, speaker_volume, screen_brightness
            FROM device_configurations WHERE device_id = ?
            """,
            ("device-1",),
        ).fetchone() == (3, 2, 68, 74)

    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            CURRENT_HEAD,
        )


def test_device_config_contract_migration_recovers_from_partial_mysql_style_ddl(
    tmp_path: Path,
) -> None:
    """MySQL DDL is non-transactional, so a failed migration can leave columns behind."""
    database = tmp_path / "device-config-contract-partial.db"
    _alembic(database, "upgrade", PREVIOUS_REVISION)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE devices ADD COLUMN hardware_profile_id VARCHAR(80)")
        connection.execute("ALTER TABLE devices ADD COLUMN display_profile_id VARCHAR(80)")
        connection.execute("ALTER TABLE devices ADD COLUMN profile_schema_version INTEGER")
        connection.execute("ALTER TABLE devices ADD COLUMN profile_sha256 VARCHAR(64)")
        connection.execute(
            "ALTER TABLE devices ADD COLUMN device_config_schema_version INTEGER "
            "NOT NULL DEFAULT 1"
        )
        connection.execute(
            "ALTER TABLE device_configurations ADD COLUMN schema_version INTEGER "
            "NOT NULL DEFAULT 1"
        )
        connection.commit()

    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            CURRENT_HEAD,
        )
        assert {"schema_version", "desired_values", "applied_values"} <= _columns(
            connection, "device_configurations"
        )
