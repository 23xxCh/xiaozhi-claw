#!/usr/bin/env python3
"""Resolve only the images approved by a versioned Hensun factory profile."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FactoryFlashPlanError(ValueError):
    pass


@dataclass(frozen=True)
class FactoryRegion:
    name: str
    offset: int
    size: int


@dataclass(frozen=True)
class FactoryProfileSegment(FactoryRegion):
    metadata_key: str


@dataclass(frozen=True)
class FactoryReleaseProfile:
    schema_version: int
    profile_id: str
    board_type: str
    hardware_version: str
    chip: str
    flash_size: str
    bootstrap_url: str
    wake_word_version: str
    microphone_profile: str
    display_profile: str
    required_sdkconfig: tuple[str, ...]
    identity: FactoryRegion
    wifi_nvs: FactoryRegion
    segments: tuple[FactoryProfileSegment, ...]


@dataclass(frozen=True)
class FactoryFlashSegment:
    name: str
    offset: int
    path: Path
    maximum_size: int


@dataclass(frozen=True)
class FactoryFlashPlan:
    profile_id: str
    board_type: str
    chip: str
    before: str
    after: str
    flash_mode: str
    flash_size: str
    flash_freq: str
    identity: FactoryRegion
    wifi_nvs: FactoryRegion
    segments: tuple[FactoryFlashSegment, ...]


def _required_text(mapping: dict[str, Any], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FactoryFlashPlanError(f"{context} requires a non-empty {key}")
    return value.strip()


def _required_int(mapping: dict[str, Any], key: str, *, context: str) -> int:
    value = mapping.get(key)
    try:
        parsed = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise FactoryFlashPlanError(f"{context} requires a valid {key}") from exc
    if parsed < 0:
        raise FactoryFlashPlanError(f"{context} requires a non-negative {key}")
    return parsed


def _load_region(payload: dict[str, Any], *, context: str) -> FactoryRegion:
    return FactoryRegion(
        name=_required_text(payload, "name", context=context),
        offset=_required_int(payload, "offset", context=context),
        size=_required_int(payload, "size", context=context),
    )


def _overlaps(left: FactoryRegion, right: FactoryRegion) -> bool:
    return left.offset < right.offset + right.size and right.offset < left.offset + left.size


def load_factory_release_profile(path: str | Path) -> FactoryReleaseProfile:
    profile_path = Path(path).resolve()
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactoryFlashPlanError(f"invalid factory profile: {profile_path}") from exc
    if not isinstance(payload, dict):
        raise FactoryFlashPlanError("factory profile must contain an object")
    if payload.get("schema_version") != 1:
        raise FactoryFlashPlanError("factory profile schema_version must be 1")

    target = payload.get("target")
    regions = payload.get("regions")
    segment_payloads = payload.get("segments")
    required_sdkconfig = payload.get("required_sdkconfig")
    if not isinstance(target, dict) or not isinstance(regions, dict):
        raise FactoryFlashPlanError("factory profile lacks target or regions")
    if not isinstance(segment_payloads, list) or not segment_payloads:
        raise FactoryFlashPlanError("factory profile requires segments")
    if not isinstance(required_sdkconfig, list) or not required_sdkconfig or not all(
        isinstance(item, str) and item.strip() for item in required_sdkconfig
    ):
        raise FactoryFlashPlanError("factory profile requires required_sdkconfig")

    identity_payload = regions.get("identity")
    wifi_payload = regions.get("wifi_nvs")
    if not isinstance(identity_payload, dict) or not isinstance(wifi_payload, dict):
        raise FactoryFlashPlanError("factory profile lacks identity or wifi_nvs region")
    identity = _load_region(identity_payload, context="identity")
    wifi_nvs = _load_region(wifi_payload, context="wifi_nvs")

    segments: list[FactoryProfileSegment] = []
    for index, item in enumerate(segment_payloads):
        if not isinstance(item, dict):
            raise FactoryFlashPlanError(f"segment {index} must contain an object")
        context = f"segment {index}"
        segments.append(
            FactoryProfileSegment(
                name=_required_text(item, "name", context=context),
                metadata_key=_required_text(item, "metadata_key", context=context),
                offset=_required_int(item, "offset", context=context),
                size=_required_int(item, "maximum_size", context=context),
            )
        )

    names = [item.name for item in segments]
    if len(names) != len(set(names)):
        raise FactoryFlashPlanError("factory profile contains duplicate segment names")
    all_regions: list[FactoryRegion] = [identity, wifi_nvs, *segments]
    for index, left in enumerate(all_regions):
        if left.size <= 0:
            raise FactoryFlashPlanError(f"{left.name} must have a positive size")
        for right in all_regions[index + 1 :]:
            if _overlaps(left, right):
                raise FactoryFlashPlanError(
                    f"factory regions overlap: {left.name} and {right.name}"
                )

    return FactoryReleaseProfile(
        schema_version=1,
        profile_id=_required_text(payload, "profile_id", context="factory profile"),
        board_type=_required_text(payload, "board_type", context="factory profile"),
        hardware_version=_required_text(
            payload, "hardware_version", context="factory profile"
        ),
        chip=_required_text(target, "chip", context="target"),
        flash_size=_required_text(target, "flash_size", context="target"),
        bootstrap_url=_required_text(
            payload, "bootstrap_url", context="factory profile"
        ),
        wake_word_version=_required_text(
            payload, "wake_word_version", context="factory profile"
        ),
        microphone_profile=_required_text(
            payload, "microphone_profile", context="factory profile"
        ),
        display_profile=_required_text(
            payload, "display_profile", context="factory profile"
        ),
        required_sdkconfig=tuple(item.strip() for item in required_sdkconfig),
        identity=identity,
        wifi_nvs=wifi_nvs,
        segments=tuple(segments),
    )


def _resolve_segment(
    build_dir: Path,
    metadata: dict[str, Any],
    profile_segment: FactoryProfileSegment,
) -> FactoryFlashSegment:
    entry = metadata.get(profile_segment.metadata_key)
    if not isinstance(entry, dict):
        raise FactoryFlashPlanError(
            f"flasher_args.json is missing {profile_segment.metadata_key} metadata"
        )
    offset_text = _required_text(entry, "offset", context=profile_segment.name)
    try:
        offset = int(offset_text, 0)
    except ValueError as exc:
        raise FactoryFlashPlanError(
            f"{profile_segment.name} has an invalid offset: {offset_text}"
        ) from exc
    if offset != profile_segment.offset:
        raise FactoryFlashPlanError(
            f"{profile_segment.name} offset must be 0x{profile_segment.offset:x}, "
            f"got 0x{offset:x}"
        )

    relative_path = Path(_required_text(entry, "file", context=profile_segment.name))
    candidate = (build_dir / relative_path).resolve()
    try:
        candidate.relative_to(build_dir)
    except ValueError as exc:
        raise FactoryFlashPlanError(
            f"{profile_segment.name} path is outside build directory"
        ) from exc
    if not candidate.is_file():
        raise FactoryFlashPlanError(
            f"{profile_segment.name} image does not exist: {candidate}"
        )
    if candidate.stat().st_size > profile_segment.size:
        raise FactoryFlashPlanError(
            f"{profile_segment.name} image exceeds its partition: "
            f"{candidate.stat().st_size} > {profile_segment.size}"
        )
    return FactoryFlashSegment(
        profile_segment.name,
        offset,
        candidate,
        profile_segment.size,
    )


def resolve_factory_flash_plan(
    build_directory: str | Path, profile_path: str | Path
) -> FactoryFlashPlan:
    profile = load_factory_release_profile(profile_path)
    build_dir = Path(build_directory).resolve()
    args_path = build_dir / "flasher_args.json"
    if not args_path.is_file():
        raise FactoryFlashPlanError(f"missing ESP-IDF flash metadata: {args_path}")
    try:
        metadata = json.loads(args_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactoryFlashPlanError(f"invalid ESP-IDF flash metadata: {args_path}") from exc
    if not isinstance(metadata, dict):
        raise FactoryFlashPlanError("flasher_args.json must contain an object")

    sdkconfig_candidates = (build_dir / "sdkconfig", build_dir.parent / "sdkconfig")
    sdkconfig_path = next((item for item in sdkconfig_candidates if item.is_file()), None)
    if sdkconfig_path is None:
        raise FactoryFlashPlanError("build lacks sdkconfig for factory profile verification")
    sdkconfig_lines = {
        line.strip()
        for line in sdkconfig_path.read_text(encoding="utf-8", errors="replace").splitlines()
    }
    missing_config = [
        item for item in profile.required_sdkconfig if item not in sdkconfig_lines
    ]
    if missing_config:
        raise FactoryFlashPlanError(
            "build is missing required sdkconfig: " + ", ".join(missing_config)
        )

    settings = metadata.get("flash_settings")
    extra = metadata.get("extra_esptool_args")
    if not isinstance(settings, dict) or not isinstance(extra, dict):
        raise FactoryFlashPlanError("flasher_args.json lacks flash settings")
    chip = _required_text(extra, "chip", context="extra_esptool_args")
    flash_size = _required_text(settings, "flash_size", context="flash_settings")
    if chip != profile.chip:
        raise FactoryFlashPlanError(
            f"factory target must be {profile.chip}, got {chip}"
        )
    if flash_size != profile.flash_size:
        raise FactoryFlashPlanError(
            f"factory target must have {profile.flash_size} flash, got {flash_size}"
        )

    segments = tuple(
        _resolve_segment(build_dir, metadata, item) for item in profile.segments
    )
    return FactoryFlashPlan(
        profile_id=profile.profile_id,
        board_type=profile.board_type,
        chip=chip,
        before=_required_text(extra, "before", context="extra_esptool_args"),
        after=_required_text(extra, "after", context="extra_esptool_args"),
        flash_mode=_required_text(settings, "flash_mode", context="flash_settings"),
        flash_size=flash_size,
        flash_freq=_required_text(settings, "flash_freq", context="flash_settings"),
        identity=profile.identity,
        wifi_nvs=profile.wifi_nvs,
        segments=segments,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = resolve_factory_flash_plan(args.build_dir, args.profile)
    except FactoryFlashPlanError as exc:
        parser.error(str(exc))

    payload = {
        "profile_id": plan.profile_id,
        "board_type": plan.board_type,
        "chip": plan.chip,
        "before": plan.before,
        "after": plan.after,
        "flash_mode": plan.flash_mode,
        "flash_size": plan.flash_size,
        "flash_freq": plan.flash_freq,
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
        "segments": [
            {
                "name": segment.name,
                "offset": f"0x{segment.offset:x}",
                "path": str(segment.path),
                "maximum_size": segment.maximum_size,
            }
            for segment in plan.segments
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
