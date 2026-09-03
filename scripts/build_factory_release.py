#!/usr/bin/env python3
"""Build an immutable, digest-locked Hensun factory release directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.factory_flash_plan import (
        FactoryFlashPlanError,
        load_factory_release_profile,
        resolve_factory_flash_plan,
    )
except ModuleNotFoundError:
    from factory_flash_plan import (  # type: ignore[no-redef]
        FactoryFlashPlanError,
        load_factory_release_profile,
        resolve_factory_flash_plan,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_factory_release(
    build_directory: str | Path,
    profile_path: str | Path,
    output_directory: str | Path,
    *,
    release_version: str,
) -> Path:
    version = release_version.strip()
    if not version or any(character in version for character in "\\/:"):
        raise FactoryFlashPlanError("release_version is empty or contains path characters")

    output_dir = Path(output_directory).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FactoryFlashPlanError(
            f"factory release directory must be empty: {output_dir}"
        )
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    profile = load_factory_release_profile(profile_path)
    plan = resolve_factory_flash_plan(build_directory, profile_path)
    segment_payloads: list[dict[str, object]] = []
    for segment in plan.segments:
        suffix = segment.path.suffix or ".bin"
        target = images_dir / f"{segment.name}{suffix}"
        shutil.copyfile(segment.path, target)
        segment_payloads.append(
            {
                "name": segment.name,
                "offset": f"0x{segment.offset:x}",
                "maximum_size": segment.maximum_size,
                "bytes": target.stat().st_size,
                "sha256": _sha256(target),
                "file": target.relative_to(output_dir).as_posix(),
            }
        )

    manifest = {
        "schema_version": 1,
        "release_version": version,
        "profile_id": profile.profile_id,
        "board_type": profile.board_type,
        "hardware_version": profile.hardware_version,
        "chip": profile.chip,
        "flash_size": profile.flash_size,
        "bootstrap_url": profile.bootstrap_url,
        "wake_word_version": profile.wake_word_version,
        "microphone_profile": profile.microphone_profile,
        "display_profile": profile.display_profile,
        "created_at": datetime.now(UTC).isoformat(),
        "flash_settings": {
            "before": plan.before,
            "after": plan.after,
            "mode": plan.flash_mode,
            "frequency": plan.flash_freq,
        },
        "identity": {
            "name": plan.identity.name,
            "offset": f"0x{plan.identity.offset:x}",
            "size": f"0x{plan.identity.size:x}",
        },
        "wifi_nvs": {
            "name": plan.wifi_nvs.name,
            "offset": f"0x{plan.wifi_nvs.offset:x}",
            "size": f"0x{plan.wifi_nvs.size:x}",
        },
        "segments": segment_payloads,
    }
    manifest_path = output_dir / "factory-release.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--release-version", required=True)
    args = parser.parse_args()
    try:
        manifest = build_factory_release(
            args.build_dir,
            args.profile,
            args.output_dir,
            release_version=args.release_version,
        )
    except FactoryFlashPlanError as exc:
        parser.error(str(exc))
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
