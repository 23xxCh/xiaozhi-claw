"""Run an isolated, bounded realtime gateway soak test.

The default mode uses mock providers and a temporary SQLite database.  It is
safe to leave running overnight: no production database, device NVS, provider
API key, audio file, or transcript is touched.  A separate optional health
URL can probe an already-running LAN gateway without opening a second device
session.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]


def _json_request(url: str, *, method: str = "GET", payload: dict[str, object] | None = None,
                 headers: dict[str, str] | None = None) -> dict[str, object]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=body, method=method, headers=headers or {})
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=5) as response:  # noqa: S310 - caller controls localhost/LAN URL
        return json.load(response)


def _wait_ready(urls: list[str], deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            if all(_json_request(url).get("status") in {"ok", "ready"} for url in urls):
                return
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise TimeoutError("isolated services did not become ready")


def _start_services(
    database: Path, control_port: int, gateway_port: int
) -> tuple[list[subprocess.Popen[str]], list[object]]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
            "PROVIDER_MODE": "mock",
            "ADMIN_API_KEY": "soak-admin-key",
            "JWT_SECRET": "soak-jwt-secret-with-enough-entropy",
            "DEVICE_CREDENTIAL_PEPPER": "soak-device-pepper-with-enough-entropy",
            "MEMORY_MASTER_KEY": "soak-memory-key-with-enough-entropy",
            "DEVICE_WS_URL": f"ws://127.0.0.1:{gateway_port}/v1/device/ws",
        }
    )
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    logs = database.parent
    commands = (
        (
            "control",
            [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
             "--port", str(control_port)],
        ),
        (
            "gateway",
            [sys.executable, "-m", "uvicorn", "backend.realtime.main:app", "--host", "127.0.0.1",
             "--port", str(gateway_port)],
        ),
    )
    processes: list[subprocess.Popen[str]] = []
    handles: list[object] = []
    for name, command in commands:
        log = (logs / f"{name}.log").open("w", encoding="utf-8")
        handles.append(log)
        processes.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                env=environment,
                creationflags=flags,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
        )
    return processes, handles


def _stop_services(processes: list[subprocess.Popen[str]], handles: list[object]) -> None:
    for process in processes:
        if process.poll() is None:
            if sys.platform == "win32":
                # uvicorn may have a child worker holding the redirected log
                # handle; taskkill's exact process tree is limited to this
                # isolated soak process, never the user's LAN services.
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for handle in handles:
        handle.close()  # type: ignore[attr-defined]


def _read_device_snapshot(database: Path) -> dict[str, object]:
    """Read only aggregate online state from the existing LAN DB.

    SQLite is opened in read-only URI mode.  This is deliberately an aggregate
    probe: no serial number, token, transcript, or audio data enters the soak
    report, and a locked/unavailable production DB is reported as a probe
    failure instead of changing it.
    """
    if not database.exists():
        return {"status": "unavailable", "reason": "database-not-found"}
    connection: sqlite3.Connection | None = None
    try:
        uri = f"file:{database.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        device_count = int(connection.execute("select count(*) from devices").fetchone()[0])
        row = connection.execute(
            "select count(*), max(heartbeat_at), "
            "sum(case when heartbeat_at >= datetime('now', '-90 seconds') then 1 else 0 end) "
            "from device_sessions where status = 'online'"
        ).fetchone()
        return {
            "status": "ok",
            "device_count": device_count,
            "online_session_count": int(row[0] or 0),
            "latest_online_heartbeat": row[1],
            "fresh_online_session_count_90s": int(row[2] or 0),
        }
    except (OSError, sqlite3.Error) as exc:
        return {"status": "error", "reason": type(exc).__name__}
    finally:
        if connection is not None:
            connection.close()


def _process_snapshot(processes: list[subprocess.Popen[str]]) -> dict[str, object]:
    """Capture optional process memory without making psutil mandatory."""
    snapshot: dict[str, object] = {"active_tasks": len(asyncio.all_tasks())}
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        snapshot["psutil"] = False
        if sys.platform == "win32":
            rss: dict[str, int] = {}
            cpu: dict[str, int] = {}
            for name, process in zip(("control", "gateway"), processes, strict=False):
                metrics = _windows_process_metrics(process.pid)
                if metrics is not None:
                    rss[name] = metrics[0]
                    cpu[name] = metrics[1]
            snapshot["rss_bytes"] = rss
            snapshot["cpu_time_100ns"] = cpu
        return snapshot
    snapshot["psutil"] = True
    rss: dict[str, int] = {}
    cpu: dict[str, float] = {}
    for name, process in zip(("control", "gateway"), processes, strict=False):
        try:
            root = psutil.Process(process.pid)
            family = [root, *root.children(recursive=True)]
            rss[name] = sum(int(item.memory_info().rss) for item in family)
            cpu[name] = round(
                sum(item.cpu_times().user + item.cpu_times().system for item in family),
                3,
            )
        except (psutil.Error, OSError, ValueError):
            continue
    snapshot["rss_bytes"] = rss
    snapshot["cpu_seconds"] = cpu
    return snapshot


def _windows_process_metrics(pid: int) -> tuple[int, int] | None:
    """Return working-set bytes and cumulative CPU ticks without extra packages."""
    if sys.platform != "win32":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]

        def ticks(self) -> int:
            return (int(self.high) << 32) | int(self.low)

    kernel32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    handle = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        memory = ProcessMemoryCounters()
        memory.cb = ctypes.sizeof(memory)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb):
            return None
        creation, exit_time, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        return int(memory.WorkingSetSize), kernel.ticks() + user.ticks()
    finally:
        kernel32.CloseHandle(handle)


def _read_soak_database_counts(database: Path) -> dict[str, int]:
    connection = sqlite3.connect(database, timeout=2)
    try:
        result: dict[str, int] = {}
        for table in (
            "conversation_sessions",
            "usage_events",
            "provider_usage",
            "device_sessions",
        ):
            result[table] = int(
                connection.execute(f"select count(*) from {table}").fetchone()[0]
            )
        return result
    finally:
        connection.close()


def _provision(control_url: str, serial: str) -> tuple[str, str]:
    headers = {"X-Admin-Key": "soak-admin-key"}
    registered = _json_request(
        f"{control_url}/v1/admin/devices",
        method="POST",
        headers=headers,
        payload={"serial_number": serial, "board_type": "hensun-desk-v1"},
    )
    secret = str(registered["device_secret"])
    bootstrap = _json_request(
        f"{control_url}/v1/device/bootstrap",
        method="POST",
        headers={"Device-Id": serial, "Authorization": f"Bearer {secret}"},
        payload={"firmware_version": "soak"},
    )
    login = _json_request(
        f"{control_url}/v1/auth/dev-login",
        method="POST",
        payload={"openid": f"soak-{serial}", "adult_confirmed": True},
    )
    _json_request(
        f"{control_url}/v1/claims/confirm-phone",
        method="POST",
        headers={"Authorization": f"Bearer {login['access_token']}"},
        payload={"claim_code": bootstrap["claim_code"]},
    )
    return secret, str(login["access_token"])


async def _one_turn(
    ws, marker: bytes, counters: Counter[str], latency_samples: list[float]
) -> None:
    turn_started = time.perf_counter()
    await ws.send(json.dumps({"type": "hello", "version": 1}))
    hello = json.loads(await ws.recv())
    if hello.get("type") != "hello":
        raise RuntimeError("missing hello response")
    await ws.send(json.dumps({"type": "listen", "state": "start"}))
    await ws.send(marker)
    await ws.send(json.dumps({"type": "listen", "state": "stop"}))
    deadline = time.monotonic() + 10
    saw_stt = False
    while time.monotonic() < deadline:
        event = await asyncio.wait_for(ws.recv(), timeout=3)
        if isinstance(event, bytes):
            counters["audio_packets"] += 1
            continue
        payload = json.loads(event)
        event_type = payload.get("type")
        if event_type == "stt":
            saw_stt = True
            counters["stt"] += 1
        elif event_type == "error":
            counters[f"error:{payload.get('code', 'unknown')}"] += 1
            raise RuntimeError("gateway returned a stable error")
        elif event_type == "tts" and payload.get("state") == "start":
            await ws.send(
                json.dumps(
                    {"type": "tts", "state": "ready", "reply_id": payload.get("reply_id", "")}
                )
            )
        elif event_type == "tts" and payload.get("state") == "stop":
            await ws.send(
                json.dumps(
                    {"type": "tts", "state": "drained", "reply_id": payload.get("reply_id", "")}
                )
            )
            if saw_stt:
                counters["turns"] += 1
                latency_samples.append(round((time.perf_counter() - turn_started) * 1000, 2))
                return
    raise TimeoutError("turn did not reach tts.stop")


async def _run(args: argparse.Namespace, report_dir: Path) -> dict[str, object]:
    started = time.time()
    counters: Counter[str] = Counter()
    failures: list[dict[str, object]] = []
    latency_samples: list[float] = []
    metric_samples: list[dict[str, object]] = []
    device_probe_samples: list[dict[str, object]] = []
    control_url = f"http://127.0.0.1:{args.control_port}"
    gateway_url = f"ws://127.0.0.1:{args.gateway_port}/v1/device/ws"
    processes: list[subprocess.Popen[str]] = []
    handles: list[object] = []
    stop_event = asyncio.Event()

    def stop_signal(*_args: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGTERM, stop_signal)
    with tempfile.TemporaryDirectory(prefix="hensun-soak-") as temp:
        database = Path(temp) / "soak.db"
        processes, handles = _start_services(database, args.control_port, args.gateway_port)
        try:
            _wait_ready(
                [f"{control_url}/health/ready", f"http://127.0.0.1:{args.gateway_port}/health/ready"],
                time.monotonic() + 30,
            )
            serial = f"SOAK-{uuid.uuid4().hex[:12].upper()}"
            secret, _ = _provision(control_url, serial)
            deadline = time.monotonic() + args.duration_seconds
            next_probe = 0.0
            while time.monotonic() < deadline and not stop_event.is_set():
                now = time.monotonic()
                if now >= next_probe:
                    counters["local_health_probes"] += 1
                    try:
                        _wait_ready(
                            [f"{control_url}/health/ready", f"http://127.0.0.1:{args.gateway_port}/health/ready"],
                            time.monotonic() + 3,
                        )
                    except Exception as exc:
                        counters["health_failures"] += 1
                        failures.append({"kind": "health", "error": type(exc).__name__})
                    process_metrics = _process_snapshot(processes)
                    process_metrics["database_rows"] = _read_soak_database_counts(database)
                    metric_samples.append(process_metrics)
                    if args.device_db:
                        device_snapshot = _read_device_snapshot(args.device_db)
                        device_probe_samples.append(device_snapshot)
                        if device_snapshot.get("status") != "ok":
                            counters["device_probe_failures"] += 1
                            failures.append(
                                {
                                    "kind": "device-readonly-probe",
                                    "error": str(device_snapshot.get("reason", "unknown")),
                                }
                            )
                        else:
                            counters["device_probes"] += 1
                    if args.probe_url:
                        try:
                            _json_request(args.probe_url)
                            counters["lan_probes"] += 1
                        except Exception as exc:
                            counters["lan_probe_failures"] += 1
                            failures.append({"kind": "lan-health", "error": type(exc).__name__})
                    next_probe = now + args.probe_interval_seconds
                try:
                    async with connect(
                        gateway_url,
                        additional_headers={
                            "Device-Id": serial,
                            "Authorization": f"Bearer {secret}",
                        },
                        open_timeout=5,
                    ) as ws:
                        await _one_turn(
                            ws,
                            f"soak-{counters['turns'] + 1}".encode(),
                            counters,
                            latency_samples,
                        )
                except Exception as exc:
                    counters["turn_failures"] += 1
                    failures.append({"kind": "turn", "error": type(exc).__name__})
                await asyncio.sleep(max(0.0, args.interval_seconds))
        finally:
            _stop_services(processes, handles)
    memory_growth: dict[str, int] = {}
    for process_name in ("control", "gateway"):
        samples = [
            int(sample.get("rss_bytes", {}).get(process_name, 0))
            for sample in metric_samples
            if isinstance(sample.get("rss_bytes"), dict)
            and sample.get("rss_bytes", {}).get(process_name)
        ]
        if len(samples) >= 3:
            split = max(1, len(samples) // 3)
            baseline = statistics.median(samples[:split])
            recent = statistics.median(samples[-split:])
            growth = int(recent - baseline)
            memory_growth[process_name] = growth
            if growth > max(32 * 1024 * 1024, baseline * 0.3):
                failures.append({"kind": "memory-growth", "process": process_name})
    device_online_rate: float | None = None
    valid_device_samples = [
        sample for sample in device_probe_samples if sample.get("status") == "ok"
    ]
    if valid_device_samples:
        online_samples = sum(
            int(sample.get("fresh_online_session_count_90s", 0)) > 0
            for sample in valid_device_samples
        )
        device_online_rate = online_samples / len(valid_device_samples)
        if device_online_rate < 0.99:
            failures.append({"kind": "device-online-rate", "rate": device_online_rate})
    ended = time.time()
    return {
        "started_at": datetime.fromtimestamp(started, UTC).isoformat(),
        "ended_at": datetime.fromtimestamp(ended, UTC).isoformat(),
        "duration_seconds": round(ended - started, 1),
        "counters": dict(counters),
        "failures": failures[-50:],
        "pass": not failures and counters.get("turns", 0) > 0,
        "latency_ms": {
            "p50": round(statistics.median(latency_samples), 2) if latency_samples else None,
            "p95": round(
                sorted(latency_samples)[max(0, (len(latency_samples) * 95 + 99) // 100 - 1)], 2
            )
            if latency_samples
            else None,
            "samples": len(latency_samples),
        },
        "metrics": metric_samples[-100:],
        "memory_growth_bytes": memory_growth,
        "device_readonly_probe": {
            "requested": bool(args.device_db),
            "samples": device_probe_samples[-100:],
            "online_rate": device_online_rate,
            "mcp_call": "not-requested",
        },
        "configuration": {
            "provider_mode": "mock",
            "real_model_calls": False,
            "duration_seconds": args.duration_seconds,
            "interval_seconds": args.interval_seconds,
            "probe_url": bool(args.probe_url),
            "device_db": bool(args.device_db),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=int, default=8 * 60 * 60)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--probe-interval-seconds", type=float, default=3600.0)
    parser.add_argument("--probe-url", default="")
    parser.add_argument("--device-db", type=Path, default=None)
    parser.add_argument("--control-port", type=int, default=8110)
    parser.add_argument("--gateway-port", type=int, default=8111)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "run" / "soak")
    args = parser.parse_args()
    if args.duration_seconds <= 0 or args.interval_seconds < 0:
        parser.error("duration must be positive and interval must not be negative")
    report_dir = args.report_dir / datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = asyncio.run(_run(args, report_dir))
    except Exception as exc:
        report = {"pass": False, "fatal_error": type(exc).__name__, "message": str(exc)[:200]}
    (report_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    counters = report.get("counters", {})
    latency = report.get("latency_ms", {})
    lines = [
        "# Hensun 夜间稳定性报告",
        "",
        f"- 结论：**{'通过' if report.get('pass') else '失败或未完成'}**",
        f"- 开始：`{report.get('started_at', 'unknown')}`",
        f"- 结束：`{report.get('ended_at', 'unknown')}`",
        f"- 运行时长：`{report.get('duration_seconds', 'unknown')} 秒`",
        "",
        "## 关键指标",
        "",
        f"- 完成轮数：`{counters.get('turns', 0)}`；轮次失败：`{counters.get('turn_failures', 0)}`",
        f"- 音频包：`{counters.get('audio_packets', 0)}`；STT 事件：`{counters.get('stt', 0)}`",
        f"- 轮次延迟 P50/P95：`{latency.get('p50')} / {latency.get('p95')} ms`",
        f"- 健康探测失败：`{counters.get('health_failures', 0)}`",
        f"- 内存增长（控制面/网关）：`{report.get('memory_growth_bytes', {})}`",
        (
            f"- 只读设备探测：`{counters.get('device_probes', 0)}`；"
            f"失败：`{counters.get('device_probe_failures', 0)}`"
        ),
        f"- 真机在线率：`{report.get('device_readonly_probe', {}).get('online_rate')}`",
        "",
        "## 边界说明",
        "",
        "- 使用临时 SQLite 和 mock provider；未调用 Qwen、DeepSeek 或 TTS。",
        "- 未刷写 ESP32、未擦除 NVS、未写入现有 `hensun-lan.db`。",
        "- 真机探测仅读取现有数据库聚合心跳；没有触发麦克风、播放或 MCP 工具调用。",
        "- 详细失败样本、指标和配置见同目录 `report.json`。",
    ]
    (report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
