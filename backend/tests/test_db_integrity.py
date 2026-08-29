from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.db import create_engine


@pytest.mark.asyncio
async def test_sqlite_connections_enforce_foreign_keys(tmp_path: Path) -> None:
    database = tmp_path / "foreign-keys.db"
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    try:
        async with engine.begin() as connection:
            enabled = await connection.scalar(text("PRAGMA foreign_keys"))
            assert enabled == 1
            await connection.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
            await connection.execute(
                text(
                    "CREATE TABLE child ("
                    "id INTEGER PRIMARY KEY, "
                    "parent_id INTEGER NOT NULL REFERENCES parent(id)"
                    ")"
                )
            )

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text("INSERT INTO child (id, parent_id) VALUES (1, 999)")
                )
    finally:
        await engine.dispose()
