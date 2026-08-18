#!/usr/bin/env python3
"""Resolve the only firmware segments allowed in a preserve-identity flash."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class SafeFlashPlanError(ValueError):
    pass


@dataclass(frozen=True)
class FlashSegment:
    name: str
    offset: int
    path: Path


@dataclass(frozen=True)
class SafeFlashPlan:
    chip: str
    before: str
    after: str
    flash_mode: str
    flash_size: str
    flash_freq: str
    segments: tuple[FlashSegment, ...]


def _required_text(mapping: dict[str, Any], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SafeFlashPlanError(f"{context} requires a non-empty {key}")
    return value.strip()


def _resolve_segment(
    build_dir: Path, metadata: dict[str, Any], name: str
) -> FlashSegment:
    entry = metadata.get(name)
    if not isinstance(entry, dict):
        raise SafeFlashPlanError(f"flasher_args.json is missing {name} metadata")
    offset_text = _required_text(entry, "offset", context=name)
    try:
        offset = int(offset_text, 0)
    except ValueError as exc:
        raise SafeFlashPlanError(f"{name} has an invalid offset: {offset_text}") from exc
    if offset < 0:
        raise SafeFlashPlanError(f"{name} offset must not be negative")

    relative_path = Path(_required_text(entry, "file", context=name))
    candidate = (build_dir / relative_path).resolve()
    try:
        candidate.relative_to(build_dir)
    except ValueError as exc:
        raise SafeFlashPlanError(f"{name} path is outside build directory") from exc
    if not candidate.is_file():
        raise SafeFlashPlanError(f"{name} image does not exist: {candidate}")
    return FlashSegment(name=name, offset=offset, path=candidate)


def resolve_safe_flash_plan(
    build_directory: str | Path, *, require_emote: bool
) -> SafeFlashPlan:
    build_dir = Path(build_directory).resolve()
    args_path = build_dir / "flasher_args.json"
    if not args_path.is_file():
        raise SafeFlashPlanError(f"missing ESP-IDF flash metadata: {args_path}")
    try:
        metadata = json.loads(args_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SafeFlashPlanError(f"invalid ESP-IDF flash metadata: {args_path}") from exc
    if not isinstance(metadata, dict):
        raise SafeFlashPlanError("flasher_args.json must contain an object")

    settings = metadata.get("flash_settings")
    extra = metadata.get("extra_esptool_args")
    if not isinstance(settings, dict) or not isinstance(extra, dict):
        raise SafeFlashPlanError("flasher_args.json lacks flash settings")

    segments = [_resolve_segment(build_dir, metadata, "app")]
    if "emote_gen" in metadata:
        segments.append(_resolve_segment(build_dir, metadata, "emote_gen"))
    elif require_emote:
        raise SafeFlashPlanError("local landscape firmware requires emote_gen metadata")
    if len({segment.offset for segment in segments}) != len(segments):
        raise SafeFlashPlanError("safe flash segments have duplicate offsets")

    return SafeFlashPlan(
        chip=_required_text(extra, "chip", context="extra_esptool_args"),
        before=_required_text(extra, "before", context="extra_esptool_args"),
        after=_required_text(extra, "after", context="extra_esptool_args"),
        flash_mode=_required_text(settings, "flash_mode", context="flash_settings"),
        flash_size=_required_text(settings, "flash_size", context="flash_settings"),
        flash_freq=_required_text(settings, "flash_freq", context="flash_settings"),
        segments=tuple(segments),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--require-emote", action="store_true")
    args = parser.parse_args()
    try:
        plan = resolve_safe_flash_plan(args.build_dir, require_emote=args.require_emote)
    except SafeFlashPlanError as exc:
        parser.error(str(exc))

    payload = asdict(plan)
    payload["segments"] = [
        {
            "name": segment.name,
            "offset": f"0x{segment.offset:x}",
            "path": str(segment.path),
        }
        for segment in plan.segments
    ]
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
