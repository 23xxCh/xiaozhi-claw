from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_factory_release import build_factory_release
from scripts.factory_flash_plan import (
    FactoryFlashPlanError,
    load_factory_release_profile,
    resolve_factory_flash_plan,
)

ROOT = Path(__file__).resolve().parents[2]


EXPECTED = {
    "bootloader": (0x0, "bootloader/bootloader.bin"),
    "partition-table": (0x8000, "partition_table/partition-table.bin"),
    "otadata": (0xD000, "ota_data_initial.bin"),
    "app": (0x20000, "xiaozhi.bin"),
    "assets": (0x804000, "assets.bin"),
}


PROFILE = ROOT / "scripts/factory_profiles/hensun-nocam-pilot-v1.json"


def write_flasher_args(build_dir: Path) -> None:
    payload: dict[str, object] = {
        "flash_settings": {
            "flash_mode": "dio",
            "flash_size": "16MB",
            "flash_freq": "80m",
        },
        "extra_esptool_args": {
            "after": "hard-reset",
            "before": "default-reset",
            "chip": "esp32s3",
        },
    }
    for name, (offset, relative) in EXPECTED.items():
        path = build_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        payload[name] = {"offset": hex(offset), "file": relative}
    (build_dir / "flasher_args.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    (build_dir / "sdkconfig").write_text(
        "\n".join(profile["required_sdkconfig"]) + "\n", encoding="utf-8"
    )


def test_factory_plan_contains_exactly_the_six_public_firmware_segments(
    tmp_path: Path,
) -> None:
    write_flasher_args(tmp_path)

    plan = resolve_factory_flash_plan(tmp_path, PROFILE)

    assert [(item.name, item.offset) for item in plan.segments] == [
        (name, offset) for name, (offset, _) in EXPECTED.items()
    ]
    assert all(item.path.is_file() for item in plan.segments)
    assert not {"nvs", "hensun_keys"}.intersection(
        item.name for item in plan.segments
    )


def test_factory_plan_rejects_shifted_or_missing_partitions(tmp_path: Path) -> None:
    write_flasher_args(tmp_path)
    metadata = json.loads((tmp_path / "flasher_args.json").read_text())
    metadata["assets"]["offset"] = "0x805000"
    (tmp_path / "flasher_args.json").write_text(json.dumps(metadata))

    with pytest.raises(FactoryFlashPlanError, match="assets offset"):
        resolve_factory_flash_plan(tmp_path, PROFILE)


def test_factory_plan_rejects_wrong_chip_or_flash_size(tmp_path: Path) -> None:
    write_flasher_args(tmp_path)
    metadata = json.loads((tmp_path / "flasher_args.json").read_text())
    metadata["extra_esptool_args"]["chip"] = "esp32"
    (tmp_path / "flasher_args.json").write_text(json.dumps(metadata))

    with pytest.raises(FactoryFlashPlanError, match="esp32s3"):
        resolve_factory_flash_plan(tmp_path, PROFILE)


def test_factory_plan_rejects_build_from_an_unapproved_board_config(
    tmp_path: Path,
) -> None:
    write_flasher_args(tmp_path)
    (tmp_path / "sdkconfig").write_text("CONFIG_IDF_TARGET_ESP32S3=y\n")

    with pytest.raises(FactoryFlashPlanError, match="required sdkconfig"):
        resolve_factory_flash_plan(tmp_path, PROFILE)


def test_noncam_profile_locks_identity_assets_and_wifi_regions() -> None:
    profile = load_factory_release_profile(PROFILE)

    assert profile.profile_id == "hensun-nocam-pilot-v1"
    assert profile.board_type == "hensun-nocam-pilot-v1"
    assert profile.chip == "esp32s3"
    assert profile.flash_size == "16MB"
    assert (profile.identity.offset, profile.identity.size) == (0x800000, 0x4000)
    assert (profile.wifi_nvs.offset, profile.wifi_nvs.size) == (0x9000, 0x4000)
    assert [(item.name, item.offset) for item in profile.segments][-2:] == [
        ("app", 0x20000),
        ("assets", 0x804000),
    ]
    assert profile.bootstrap_url == (
        "https://staging.hensun-desk.top/v1/device/xiaozhi-bootstrap"
    )
    assert profile.microphone_profile == "inmp441-left-24-to-s16-gain4-v2"
    assert "CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1=y" in profile.required_sdkconfig


def test_release_builder_copies_approved_segments_and_records_sha256(
    tmp_path: Path,
) -> None:
    build_dir = tmp_path / "build"
    release_dir = tmp_path / "release"
    write_flasher_args(build_dir)

    manifest_path = build_factory_release(
        build_dir,
        PROFILE,
        release_dir,
        release_version="2026.09.03-test",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["profile_id"] == "hensun-nocam-pilot-v1"
    assert manifest["release_version"] == "2026.09.03-test"
    assert {item["name"] for item in manifest["segments"]} == set(EXPECTED)
    for item in manifest["segments"]:
        image = release_dir / item["file"]
        assert image.is_file()
        assert item["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert "device_secret" not in manifest_path.read_text(encoding="utf-8")


def test_factory_entrypoint_uses_reviewed_release_and_never_rebuilds() -> None:
    source = (ROOT / "scripts/factory_flash.ps1").read_text(encoding="utf-8")

    assert "ConfirmFactoryReset" in source
    assert "ReleaseManifestPath" in source
    assert "Release segment SHA-256 does not match" in source
    assert "Resolve-Ch340Port" in source
    assert "flash-id" in source
    assert "build_firmware.ps1" not in source
    assert "$release.wifi_nvs.offset" in source
    assert "$release.identity.offset" in source
    assert "flash_device_identity.ps1" in source
    assert "verify-flash" in source
    assert "DeviceSecret" not in source.split("ConvertTo-Json", 1)[-1]


def test_customer_card_covers_softap_binding_and_wifi_change() -> None:
    card = (ROOT / "docs/customer/internal-pilot-quick-start.md").read_text(
        encoding="utf-8"
    )

    for marker in (
        "Xiaozhi-XXXX",
        "http://192.168.4.1",
        "2.4GHz",
        "扫描设备屏幕上的绑定二维码",
        "6 位备用码",
        "/claim#code=",
        "staging.hensun-desk.top",
        "长按 BOOT",
    ):
        assert marker in card
    assert "设备密钥" not in card


def test_admin_factory_batch_exports_the_selected_noncam_board_type() -> None:
    source = (ROOT / "web/app/admin/page.tsx").read_text(encoding="utf-8")

    assert "setBatchBoardType" in source
    assert 'value="hensun-nocam-pilot-v1"' in source
    assert "serial_number,board_type,device_secret" in source
    assert "board_type: batchBoardType" in source


def test_windows_factory_tool_encrypts_identity_and_requires_complete_qc() -> None:
    source = (ROOT / "scripts/Hensun-Factory-Tool.ps1").read_text(encoding="utf-8")

    assert "ConvertFrom-SecureString" in source
    assert "ConvertTo-SecureString" in source
    assert "encrypted_secret" in source
    assert "in_progress_mac" in source
    assert "factory_flash.ps1" in source
    assert "FactoryUnitOutcome" in source
    assert "Initialize-EspIdf" in source
    assert "质检失败并记录" in source
    assert "failure_reason" in source
    for check in ("屏幕", "麦克风", "扬声器", "按键", "联网", "3 轮真实对话"):
        assert check in source
    assert "device_secret =" not in source


def test_firmware_builder_can_create_the_noncam_golden_build() -> None:
    source = (ROOT / "scripts/build_firmware.ps1").read_text(encoding="utf-8")

    assert "$BoardType" in source
    assert '"hensun-nocam-pilot-v1"' in source
    assert '"hensun-nocam-selfhosted-v1"' in source
    assert "generated_assets.bin" in source
    assert "0x7FC000" in source
