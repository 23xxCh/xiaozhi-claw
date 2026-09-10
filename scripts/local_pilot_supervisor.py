"""Supervise only the local pilot's recorded processes; never rebuild or migrate on restart."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "run/local-pilot"
STATE = RUNTIME / "processes.json"
STATUS = RUNTIME / "supervisor.json"
STOP = RUNTIME / "supervisor.stop"
SERVICES = {
    "control": (8000, "backend.app.main:app", "/health/ready"),
    "gateway": (8001, "backend.realtime.main:app", "/health/ready"),
    "web": (3000, "next/dist/bin/next", "/login"),
}
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, data):
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def identity(pid, marker, born=None):
    try:
        process = psutil.Process(pid)
        if born is not None and process.create_time() != born:
            return None
        if not any(marker in arg.replace("\\", "/") for arg in process.cmdline()):
            return None
        if Path(process.cwd()).resolve() not in (ROOT, ROOT / "web"):
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def restart_allowed(attempts, now):
    attempts[:] = [stamp for stamp in attempts if now - stamp < 600]
    return len(attempts) < 3


def emit(event, **fields):
    with (RUNTIME / "supervisor-events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": time.time(), "event": event, **fields}) + "\n")


def healthy(port, path):
    try:
        with HTTP.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            return response.status == 200
    except OSError:
        return False


def launch(name):
    port, marker, _ = SERVICES[name]
    if any(c.status == "LISTEN" and c.laddr.port == port
           for c in psutil.net_connections(kind="tcp")):
        raise RuntimeError("port-occupied")
    if name == "web":
        node = shutil.which("node")
        if not node:
            raise RuntimeError("node-unavailable")
        command = [node, "node_modules/next/dist/bin/next", "start",
                   "--hostname", "0.0.0.0", "--port", str(port)]
    else:
        command = [sys.executable, "-X", "faulthandler", "-m", "uvicorn", marker,
                   "--host", "0.0.0.0", "--port", str(port)]
    log = RUNTIME / f"{name}-supervised-{time.time_ns()}.log"
    with log.open("w") as out, Path(str(log) + ".err").open("w") as err:
        child = subprocess.Popen(
            command, cwd=ROOT / "web" if name == "web" else ROOT,
            stdout=out, stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    state = read(STATE)
    state.update({f"{name}_pid": child.pid, f"{name}_launcher_pid": child.pid,
                  f"{name}_log": str(log)})
    write(STATE, state)
    return child


def supervise():
    # OS lock is released even if the supervisor itself crashes.
    import msvcrt

    with (RUNTIME / "supervisor.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        state = read(STATE)
        tracked, owned = {}, {}
        status = {"pid": os.getpid(), "born": psutil.Process().create_time(),
                  "started_at": time.time(), "services": {}}
        for name, (_, marker, _) in SERVICES.items():
            process = identity(state[f"{name}_pid"], marker)
            if process is None:
                raise RuntimeError(f"Cannot adopt {name}; start healthy pilot first")
            tracked[name] = (process.pid, process.create_time())
            status["services"][name] = {"attempts": [], "healthy": None}
        emit("supervisor-started", pid=os.getpid())
        sample_at = 0
        while not STOP.exists():
            for name, (port, marker, path) in SERVICES.items():
                item = status["services"][name]
                pid, born = tracked[name]
                process = identity(pid, marker, born)
                now = time.time()
                if process is not None and name in owned:
                    # Windows venv Python can launch a second interpreter.
                    # Track the listener, so a dead child is not hidden by its launcher.
                    try:
                        children = {p.pid for p in process.children(recursive=True)}
                    except psutil.Error:
                        children = set()  # Process may exit between identity and this query.
                    for connection in psutil.net_connections(kind="tcp"):
                        if (connection.status == "LISTEN" and connection.laddr.port == port
                                and connection.pid in children):
                            listener = identity(connection.pid, marker)
                            if listener is not None:
                                tracked[name] = (listener.pid, listener.create_time())
                                updated = read(STATE)
                                updated[f"{name}_pid"] = listener.pid
                                write(STATE, updated)
                            break
                if process is None:
                    if not item.get("missing"):
                        child = owned.get(name)
                        emit("process-exited", service=name, pid=pid,
                             exit_code=child.poll() if child else None)
                        item["missing"] = True
                        item["retry_at"] = now + 5
                    if now >= item["retry_at"] and restart_allowed(item["attempts"], now):
                        item["attempts"].append(now)
                        try:
                            child = launch(name)
                            owned[name] = child
                            tracked[name] = (child.pid, psutil.Process(child.pid).create_time())
                            item["missing"] = False
                            emit("process-restarted", service=name, pid=child.pid)
                        except (OSError, RuntimeError, psutil.Error) as exc:
                            emit("restart-failed", service=name, error_type=type(exc).__name__)
                        item["retry_at"] = now + 30
                    item["restart_limited"] = len(item["attempts"]) >= 3
                ready = process is not None and healthy(port, path)
                if ready != item["healthy"]:
                    emit("health-changed", service=name, healthy=ready)
                item["healthy"] = ready
                item["pid"] = tracked[name][0]
            status["checked_at"] = time.time()
            write(STATUS, status)
            if time.time() >= sample_at:
                emit("health-sample", services={n: s["healthy"]
                                               for n, s in status["services"].items()})
                sample_at = time.time() + 60
            for _ in range(5):
                if STOP.exists():
                    break
                time.sleep(1)
        emit("supervisor-stopped", pid=os.getpid())


def ensure():
    if STATUS.exists():
        previous = read(STATUS)
        if identity(previous["pid"], "local_pilot_supervisor.py", previous["born"]):
            return
    STOP.unlink(missing_ok=True)
    with (RUNTIME / "supervisor.log").open("a") as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                         stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    for _ in range(50):
        if STATUS.exists():
            current = read(STATUS)
            if identity(current["pid"], "local_pilot_supervisor.py", current["born"]):
                return
        time.sleep(0.1)
    raise RuntimeError("Supervisor failed to start; inspect supervisor.log")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ensure", action="store_true")
    args = parser.parse_args()
    ensure() if args.ensure else supervise()
