import json
from pathlib import Path

import psutil

from scripts import dual_process_smoke


def test_dual_process_voice_and_unbind_ignore_host_settings_and_clean_up(
    tmp_path, monkeypatch, capsys
) -> None:
    unrelated_database = tmp_path / "unrelated.db"
    unrelated_database.write_bytes(b"must remain untouched")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{unrelated_database.as_posix()}")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_API_KEY", "inherited-key-must-not-be-used")
    monkeypatch.setenv("PROVIDER_MODE", "custom")
    monkeypatch.setenv("DOUBAO_REALTIME_ENABLED", "true")
    monkeypatch.setenv("DOUBAO_API_KEY", "must-not-reach-a-provider")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    created: list[Path] = []
    temporary_directory = dual_process_smoke.tempfile.TemporaryDirectory

    def tracked_directory(*args, **kwargs):
        directory = temporary_directory(*args, dir=tmp_path, **kwargs)
        created.append(Path(directory.name))
        return directory

    monkeypatch.setattr(dual_process_smoke.tempfile, "TemporaryDirectory", tracked_directory)
    existing_children = {child.pid for child in psutil.Process().children(recursive=True)}
    assert dual_process_smoke.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "passed",
        "transport": "wss",
        "audio_format": "mock-utf8",
        "completed_turns": 1,
        "ready_ack": True,
        "drained_ack": True,
        "gateway_close_code": 4403,
        "outbox_status": "delivered",
        "processes": 2,
        "schema": "metadata-create-all",
    }
    assert created and all(not directory.exists() for directory in created)
    assert unrelated_database.read_bytes() == b"must remain untouched"
    assert {child.pid for child in psutil.Process().children(recursive=True)} <= existing_children
