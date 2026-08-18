from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.safe_flash_plan import SafeFlashPlanError, resolve_safe_flash_plan

ROOT = Path(__file__).resolve().parents[2]


def write_flasher_args(build_dir: Path, *, include_emote: bool = True) -> None:
    files = {
        "bootloader/bootloader.bin": b"boot",
        "partition_table/partition-table.bin": b"partitions",
        "ota_data_initial.bin": b"ota",
        "srmodels/srmodels.bin": b"models",
        "xiaozhi.bin": b"app",
    }
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
        "app": {"offset": "0x20000", "file": "xiaozhi.bin"},
        "bootloader": {
            "offset": "0x0",
            "file": "bootloader/bootloader.bin",
        },
        "partition-table": {
            "offset": "0x8000",
            "file": "partition_table/partition-table.bin",
        },
        "otadata": {"offset": "0xd000", "file": "ota_data_initial.bin"},
        "model": {"offset": "0x804000", "file": "srmodels/srmodels.bin"},
    }
    if include_emote:
        files["mmap_build/emote_lab/emote_gen/emote_gen.bin"] = b"emote"
        payload["emote_gen"] = {
            "offset": "0xb00000",
            "file": "mmap_build/emote_lab/emote_gen/emote_gen.bin",
        }
    for relative, content in files.items():
        path = build_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (build_dir / "flasher_args.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_plan_contains_only_application_and_emote_resources(tmp_path: Path) -> None:
    write_flasher_args(tmp_path)

    plan = resolve_safe_flash_plan(tmp_path, require_emote=True)

    assert [segment.name for segment in plan.segments] == ["app", "emote_gen"]
    assert [segment.offset for segment in plan.segments] == [0x20000, 0xB00000]
    assert all(segment.path.is_file() for segment in plan.segments)
    assert not {
        "bootloader",
        "partition-table",
        "otadata",
        "model",
        "nvs",
        "hensun_keys",
    }.intersection(segment.name for segment in plan.segments)


def test_plan_requires_emote_for_local_landscape_firmware(tmp_path: Path) -> None:
    write_flasher_args(tmp_path, include_emote=False)

    with pytest.raises(SafeFlashPlanError, match="emote_gen"):
        resolve_safe_flash_plan(tmp_path, require_emote=True)


def test_plan_rejects_paths_outside_the_build_directory(tmp_path: Path) -> None:
    write_flasher_args(tmp_path)
    payload = json.loads((tmp_path / "flasher_args.json").read_text(encoding="utf-8"))
    payload["app"]["file"] = "../device-secret.bin"
    (tmp_path.parent / "device-secret.bin").write_bytes(b"secret")
    (tmp_path / "flasher_args.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(SafeFlashPlanError, match="outside build directory"):
        resolve_safe_flash_plan(tmp_path, require_emote=True)


def test_plan_rejects_missing_or_malformed_application_metadata(tmp_path: Path) -> None:
    write_flasher_args(tmp_path)
    payload = json.loads((tmp_path / "flasher_args.json").read_text(encoding="utf-8"))
    payload["app"]["offset"] = "not-an-offset"
    (tmp_path / "flasher_args.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(SafeFlashPlanError, match="offset"):
        resolve_safe_flash_plan(tmp_path, require_emote=True)


def test_powershell_entrypoint_detects_ch340_and_never_runs_full_flash() -> None:
    source = (ROOT / "scripts/flash_firmware.ps1").read_text(encoding="utf-8")

    assert "Resolve-Ch340Port" in source
    assert "Win32_PnPEntity" in source
    assert "safe_flash_plan.py" in source
    assert 'segment.name -notin @("app", "emote_gen")' in source
    assert "idf.py -p $Port flash" not in source
    assert "write_flash" in source
