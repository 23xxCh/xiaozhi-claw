import sqlite3
from pathlib import Path

from .test_device_config_contract_migration import _alembic


def test_memory_epoch_migration_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "memory-epoch.db"
    _alembic(database, "upgrade", "20260907_10")
    _alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        column = next(row for row in connection.execute("PRAGMA table_info(agents)")
                      if row[1] == "memory_epoch")
        assert column[3] == 1  # NOT NULL, existing rows receive epoch zero.
        assert column[4].strip("'\"") == "0"
    _alembic(database, "check")
    _alembic(database, "downgrade", "20260907_10")
    with sqlite3.connect(database) as connection:
        assert "memory_epoch" not in {
            row[1] for row in connection.execute("PRAGMA table_info(agents)")
        }
    _alembic(database, "upgrade", "head")
