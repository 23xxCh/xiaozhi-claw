"""Generate the small shared device WSS vocabulary from one checked-in source."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts" / "device-ws-v1.json"
OUTPUTS = {
    ROOT / "backend" / "app" / "generated" / "device_ws_contract.py": "python",
    ROOT / "web" / "lib" / "generated" / "device-ws-contract.ts": "typescript",
    ROOT
    / "firmware"
    / "xiaozhi-esp32"
    / "main"
    / "boards"
    / "hensun"
    / "hensun-cam-pilot-v1"
    / "device_ws_contract_generated.h": "cpp",
}


def _load() -> dict[str, object]:
    contract = json.loads(SOURCE.read_text(encoding="utf-8"))
    if contract.get("protocol_version") != 1:
        raise ValueError("device WSS protocol_version must remain 1")
    message_types = contract.get("message_types")
    tts_states = contract.get("tts_states")
    device_stage_states = contract.get("device_stage_states")
    correlation = contract.get("optional_correlation_fields")
    if not isinstance(message_types, list) or not all(
        isinstance(item, str) for item in message_types
    ):
        raise ValueError("message_types must be a string array")
    if not isinstance(tts_states, list) or not all(isinstance(item, str) for item in tts_states):
        raise ValueError("tts_states must be a string array")
    if not isinstance(device_stage_states, list) or not all(
        isinstance(item, str) for item in device_stage_states
    ):
        raise ValueError("device_stage_states must be a string array")
    if not isinstance(correlation, list) or not all(isinstance(item, str) for item in correlation):
        raise ValueError("optional_correlation_fields must be a string array")
    if "tts" not in message_types or {"start", "ready", "stop", "drained"} != set(tts_states):
        raise ValueError("the playback handshake is a required V1 contract")
    if "turn_id" not in correlation:
        raise ValueError("turn_id must remain an optional self-hosted correlation field")
    if set(device_stage_states) != {
        "capture_started",
        "speaker_pcm_started",
        "playback_drained",
    }:
        raise ValueError("device stage states are a required V1 diagnostic extension")
    return contract


def _quoted(items: list[str]) -> str:
    return ", ".join(f'"{item}"' for item in items)


def _render_python(contract: dict[str, object]) -> str:
    messages = contract["message_types"]
    states = contract["tts_states"]
    device_stages = contract["device_stage_states"]
    fields = contract["optional_correlation_fields"]
    assert (
        isinstance(messages, list)
        and isinstance(states, list)
        and isinstance(device_stages, list)
        and isinstance(fields, list)
    )
    return "\n".join(
        [
            "# ruff: noqa: E501",
            '"""Generated device WSS V1 vocabulary. Do not edit by hand."""',
            "",
            f"DEVICE_WS_PROTOCOL_VERSION = {contract['protocol_version']}",
            f"DEVICE_WS_MESSAGE_TYPES = frozenset(({_quoted(messages)},))",
            f"TTS_STATES = frozenset(({_quoted(states)},))",
            f"DEVICE_STAGE_STATES = frozenset(({_quoted(device_stages)},))",
            f"OPTIONAL_CORRELATION_FIELDS = frozenset(({_quoted(fields)},))",
            "",
        ]
    )


def _render_typescript(contract: dict[str, object]) -> str:
    messages = contract["message_types"]
    states = contract["tts_states"]
    device_stages = contract["device_stage_states"]
    fields = contract["optional_correlation_fields"]
    assert (
        isinstance(messages, list)
        and isinstance(states, list)
        and isinstance(device_stages, list)
        and isinstance(fields, list)
    )
    return "\n".join(
        [
            "// Generated device WSS V1 vocabulary. Do not edit by hand.",
            f"export const deviceWsProtocolVersion = {contract['protocol_version']} as const;",
            f"export const deviceWsMessageTypes = [{_quoted(messages)}] as const;",
            f"export const ttsStates = [{_quoted(states)}] as const;",
            f"export const deviceStageStates = [{_quoted(device_stages)}] as const;",
            f"export const optionalCorrelationFields = [{_quoted(fields)}] as const;",
            "export type DeviceWsMessageType = (typeof deviceWsMessageTypes)[number];",
            "export type TtsState = (typeof ttsStates)[number];",
            "export type DeviceStageState = (typeof deviceStageStates)[number];",
            "",
        ]
    )


def _render_cpp(contract: dict[str, object]) -> str:
    messages = contract["message_types"]
    states = contract["tts_states"]
    device_stages = contract["device_stage_states"]
    fields = contract["optional_correlation_fields"]
    assert (
        isinstance(messages, list)
        and isinstance(states, list)
        and isinstance(device_stages, list)
        and isinstance(fields, list)
    )
    return "\n".join(
        [
            "#pragma once",
            "// Generated device WSS V1 vocabulary. Do not edit by hand.",
            "#include <array>",
            "#include <string_view>",
            "",
            "namespace hensun::device_ws {",
            f"constexpr int kProtocolVersion = {contract['protocol_version']};",
            (
                f"constexpr std::array<std::string_view, {len(messages)}> "
                f"kMessageTypes = {{{{{_quoted(messages)}}}}};"
            ),
            (
                f"constexpr std::array<std::string_view, {len(states)}> "
                f"kTtsStates = {{{{{_quoted(states)}}}}};"
            ),
            (
                f"constexpr std::array<std::string_view, {len(device_stages)}> "
                f"kDeviceStageStates = {{{{{_quoted(device_stages)}}}}};"
            ),
            (
                f"constexpr std::array<std::string_view, {len(fields)}> "
                f"kOptionalCorrelationFields = {{{{{_quoted(fields)}}}}};"
            ),
            "}  // namespace hensun::device_ws",
            "",
        ]
    )


def _render(kind: str, contract: dict[str, object]) -> str:
    if kind == "python":
        return _render_python(contract)
    if kind == "typescript":
        return _render_typescript(contract)
    return _render_cpp(contract)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        contract = _load()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"device contract generation failed: {exc}", file=sys.stderr)
        return 1

    stale = []
    for path, kind in OUTPUTS.items():
        rendered = _render(kind, contract)
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
                stale.append(path.relative_to(ROOT))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8")
    if stale:
        print("generated device contract is stale: " + ", ".join(map(str, stale)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
