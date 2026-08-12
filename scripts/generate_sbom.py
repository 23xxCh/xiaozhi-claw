"""Generate a dependency inventory in CycloneDX JSON without extra build dependencies."""

from __future__ import annotations

import json
import re
import tomllib
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "sbom.cdx.json"


def _python_components() -> list[dict[str, object]]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    components = []
    for requirement in project["dependencies"]:
        name = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0]
        components.append(
            {
                "type": "library",
                "bom-ref": f"pypi:{name.lower()}",
                "name": name,
                "version": requirement[len(name) :] or "unspecified",
                "properties": [{"name": "hensun:ecosystem", "value": "pypi"}],
            }
        )
    return components


def _web_components() -> list[dict[str, object]]:
    lock = json.loads((ROOT / "web" / "package-lock.json").read_text(encoding="utf-8"))
    components = []
    for path, package in lock["packages"].items():
        if not path.startswith("node_modules/") or not package.get("version"):
            continue
        name = path.removeprefix("node_modules/")
        component: dict[str, object] = {
            "type": "library",
            "bom-ref": f"npm:{name}@{package['version']}",
            "name": name,
            "version": package["version"],
            "properties": [{"name": "hensun:ecosystem", "value": "npm"}],
        }
        if package.get("license"):
            component["licenses"] = [{"license": {"id": package["license"]}}]
        components.append(component)
    return components


def _firmware_components() -> list[dict[str, object]]:
    lines = (ROOT / "firmware" / "xiaozhi-esp32" / "dependencies.lock").read_text(
        encoding="utf-8"
    ).splitlines()
    components: list[dict[str, object]] = []
    name: str | None = None
    for line in lines:
        match = re.match(r"^  ([^ ].*):$", line)
        if match:
            name = match.group(1)
            continue
        version = re.match(r"^    version: ['\"]?([^'\"]+)['\"]?$", line)
        if name and version:
            value = version.group(1)
            components.append(
                {
                    "type": "library",
                    "bom-ref": f"idf:{name}@{value}",
                    "name": name,
                    "version": value,
                    "properties": [{"name": "hensun:ecosystem", "value": "esp-idf"}],
                }
            )
            name = None
    return components


def main() -> None:
    components = _python_components() + _web_components() + _firmware_components()
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "hensun-ai-pilot",
                "version": "0.1.0",
            }
        },
        "components": sorted(components, key=lambda item: str(item["bom-ref"])),
    }
    OUTPUT.write_text(json.dumps(bom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{OUTPUT} ({len(components)} components)")


if __name__ == "__main__":
    main()
