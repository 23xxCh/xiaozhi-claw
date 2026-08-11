from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOARD_ROOT = (
    ROOT
    / "firmware"
    / "xiaozhi-esp32"
    / "main"
    / "boards"
    / "hensun"
)

BOARD_CONFIGS = {
    "hensun-cam-pilot-v1": BOARD_ROOT / "hensun-cam-pilot-v1" / "config.json",
    "hensun-desk-v1": BOARD_ROOT / "hensun-desk-v1" / "config.json",
}


def main() -> int:
    errors: list[str] = []
    configs = {
        board_type: json.loads(path.read_text(encoding="utf-8"))
        for board_type, path in BOARD_CONFIGS.items()
    }
    for board_type, config in configs.items():
        if config.get("type") != board_type:
            errors.append(f"firmware board type is not {board_type}")
        if config.get("manufacturer") != "hensun":
            errors.append(f"{board_type} manufacturer is not hensun")
        for build in config["builds"]:
            sdkconfig = "\n".join(build.get("sdkconfig_append", []))
            if "CONFIG_SEND_WAKE_WORD_DATA=n" not in sdkconfig:
                errors.append(f"{build['name']} still uploads wake-word audio")

    desk_sdkconfig = "\n".join(
        configs["hensun-desk-v1"]["builds"][0].get("sdkconfig_append", [])
    )
    if "CONFIG_USE_DEVICE_AEC=y" not in desk_sdkconfig:
        errors.append("hensun-desk-v1 device-side AEC is not enabled")

    cam_builds = {
        build["name"]: "\n".join(build.get("sdkconfig_append", []))
        for build in configs["hensun-cam-pilot-v1"]["builds"]
    }
    expected_cam_builds = {"hensun-cam-official-v1", "hensun-cam-selfhosted-v1"}
    if set(cam_builds) != expected_cam_builds:
        errors.append("hensun-cam-pilot-v1 does not define exactly two release channels")
    for name, sdkconfig in cam_builds.items():
        if "CONFIG_USE_HOTSPOT_WIFI_PROVISIONING=y" not in sdkconfig:
            errors.append(f"{name} hotspot provisioning is not enabled")
        if "CONFIG_USE_ESP_BLUFI_WIFI_PROVISIONING=n" not in sdkconfig:
            errors.append(f"{name} still enables BluFi provisioning")
    if "api.tenclass.net" not in cam_builds.get("hensun-cam-official-v1", ""):
        errors.append("official CAM firmware does not point to XiaoZhi")
    selfhosted_sdkconfig = cam_builds.get("hensun-cam-selfhosted-v1", "")
    if "api.hensun.invalid" not in selfhosted_sdkconfig:
        errors.append("self-hosted CAM firmware lacks its safe .invalid default")
    if "api.tenclass.net" in selfhosted_sdkconfig:
        errors.append("self-hosted CAM firmware points to the upstream cloud")

    cam_source = (
        BOARD_ROOT / "hensun-cam-pilot-v1" / "hensun_cam_pilot_v1_board.cc"
    ).read_text(encoding="utf-8")
    if "Esp32Camera" not in cam_source or "GetCamera" not in cam_source:
        errors.append("hensun-cam-pilot-v1 camera support is missing")
    for forbidden in ("PowerManager", "PowerSaveTimer"):
        if forbidden in cam_source:
            errors.append(f"hensun-cam-pilot-v1 includes forbidden feature: {forbidden}")

    required = [
        ROOT / "firmware" / "xiaozhi-esp32" / "LICENSE",
        ROOT / "firmware" / "UPSTREAM.md",
        ROOT / ".env.example",
    ]
    errors.extend(
        f"missing required file: {path.relative_to(ROOT)}" for path in required if not path.exists()
    )

    if errors:
        print("RELEASE GATE: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("RELEASE GATE: PASS (source/configuration checks only)")
    print(
        "Hardware, credentials, OTA signature, certification, and regulatory gates remain manual."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
