from unittest.mock import Mock

import psutil

from scripts import local_pilot_supervisor as supervisor


def test_restart_budget_expires_without_unbounded_restart_loop():
    attempts = [100, 200, 300]
    assert not supervisor.restart_allowed(attempts, 400)
    assert supervisor.restart_allowed(attempts, 701)
    assert attempts == [200, 300]


def test_identity_rejects_reused_pid_and_foreign_workspace(monkeypatch):
    process = Mock()
    process.create_time.return_value = 20
    process.cmdline.return_value = ["python", "backend.app.main:app"]
    process.cwd.return_value = str(supervisor.ROOT)
    monkeypatch.setattr(supervisor.psutil, "Process", lambda pid: process)
    assert supervisor.identity(1, "backend.app.main:app", 19) is None
    assert supervisor.identity(1, "backend.app.main:app", 20) is process
    process.cwd.return_value = str(supervisor.ROOT.parent)
    assert supervisor.identity(1, "backend.app.main:app", 20) is None


def test_missing_process_is_not_an_exception(monkeypatch):
    def missing(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(supervisor.psutil, "Process", missing)
    assert supervisor.identity(123, "app") is None


def test_occupied_port_never_launches_duplicate(monkeypatch):
    import pytest
    monkeypatch.setattr(supervisor.psutil, "net_connections", lambda **kw: [
        Mock(status="LISTEN", laddr=Mock(port=8000)),
    ])
    launch = Mock()
    monkeypatch.setattr(supervisor.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="port-occupied"):
        supervisor.launch("control")
    launch.assert_not_called()


def test_atomic_status_round_trip(tmp_path):
    path = tmp_path / "status.json"
    supervisor.write(path, {"healthy": True})
    supervisor.write(path, {"healthy": False})
    assert supervisor.read(path) == {"healthy": False}
    assert not path.with_suffix(".new").exists()
