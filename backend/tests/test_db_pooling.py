import asyncio

from sqlalchemy.pool import NullPool

from backend.app.db import create_engine


def test_file_sqlite_reuses_connections_instead_of_spawning_a_worker_per_poll(tmp_path):
    database = tmp_path / "pooled.db"
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    try:
        assert not isinstance(engine.sync_engine.pool, NullPool)
    finally:
        asyncio.run(engine.dispose())
