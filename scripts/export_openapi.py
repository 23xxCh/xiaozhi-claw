"""Export the versioned control-plane OpenAPI contract for web and mini-program clients."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

app = importlib.import_module("backend.app.main").app
OUTPUT = ROOT / "docs" / "openapi.json"


def main() -> None:
    OUTPUT.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
