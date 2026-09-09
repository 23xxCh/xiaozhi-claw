"""Run backend tests without inheriting local .env files or provider credentials."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dual_process_smoke import ROOT, _environment, _stop_owned_processes


def main() -> int:
    (ROOT / "run").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="backend-tests-", dir=ROOT / "run"))
    runtime = {
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
        "USERPROFILE", "LOCALAPPDATA", "APPDATA", "HOME", "LANG", "LC_ALL",
        "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "PYTHONPATH", "PYTHONUTF8",
        "PYTHONIOENCODING", "PYTHONUNBUFFERED",
    }
    # Smoke-specific application defaults must not override the tests' own settings.
    env = {key: value for key, value in _environment(directory, 0).items()
           if key.upper() in runtime}
    targets = sys.argv[1:] or [str(ROOT / "backend/tests")]
    print(f"Isolated report: {directory / 'pytest.log'}", flush=True)
    with (directory / "pytest.log").open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "pytest", *targets, "-o", "addopts=", "-q"],
            cwd=directory, env=env, stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            code = process.wait(timeout=900)
        finally:
            _stop_owned_processes([process])
    print((directory / "pytest.log").read_text(encoding="utf-8"), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
