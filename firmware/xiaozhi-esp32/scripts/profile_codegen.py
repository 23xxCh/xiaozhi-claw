"""Validate Hensun build profiles and render deterministic build inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAFE_BOOTSTRAP_PLACEHOLDER = "https://api.hensun.invalid/v1/device/xiaozhi-bootstrap"


@dataclass(frozen=True)
class RenderedProfile:
    sdkconfig: tuple[str, ...]
    header: str
    cmake: str
    metadata: str

    def write(self, output_dir: Path, *, check: bool = False) -> None:
        files = {
            "hensun_profile_generated.h": self.header,
            "hensun_profile.cmake": self.cmake,
            "hensun_profile.json": self.metadata,
        }
        if check:
            mismatches = [
                name
                for name, content in files.items()
                if not (output_dir / name).is_file()
                or (output_dir / name).read_text(encoding="utf-8") != content
            ]
            if mismatches:
                raise ValueError("Generated profile files are stale: " + ", ".join(mismatches))
            return
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (output_dir / name).write_text(content, encoding="utf-8", newline="\n")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Profile must be a JSON object: {path}")
    return value


def _resolve_pointer(schema: dict[str, Any], pointer: str) -> dict[str, Any]:
    if not pointer.startswith("#/"):
        raise ValueError(f"Only local JSON Schema references are supported: {pointer}")
    current: Any = schema
    for part in pointer[2:].split("/"):
        current = current[part.replace("~1", "/").replace("~0", "~")]
    return current


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValueError(f"Unsupported JSON Schema type: {expected}")


def _validate_schema(
    value: Any,
    rule: dict[str, Any],
    root: dict[str, Any],
    location: str = "$",
) -> None:
    if "$ref" in rule:
        _validate_schema(value, _resolve_pointer(root, rule["$ref"]), root, location)
        return
    if "const" in rule and value != rule["const"]:
        raise ValueError(f"{location} must equal {rule['const']!r}")
    if "enum" in rule and value not in rule["enum"]:
        raise ValueError(f"{location} must be one of {rule['enum']!r}")
    expected_type = rule.get("type")
    if expected_type and not _matches_type(value, expected_type):
        raise ValueError(f"{location} must be {expected_type}")
    if isinstance(value, dict):
        required = rule.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"{location} is missing: {', '.join(missing)}")
        properties = rule.get("properties", {})
        additional = rule.get("additionalProperties", True)
        if additional is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ValueError(f"{location} has unknown fields: {', '.join(unknown)}")
        if len(value) < rule.get("minProperties", 0):
            raise ValueError(f"{location} has too few properties")
        for name, item in value.items():
            child_rule = properties.get(name)
            if child_rule is None and isinstance(additional, dict):
                child_rule = additional
            if child_rule is not None:
                _validate_schema(item, child_rule, root, f"{location}.{name}")
    if isinstance(value, list) and "items" in rule:
        for index, item in enumerate(value):
            _validate_schema(item, rule["items"], root, f"{location}[{index}]")
    if isinstance(value, str) and "pattern" in rule:
        if re.fullmatch(rule["pattern"], value) is None:
            raise ValueError(f"{location} does not match {rule['pattern']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"{location} is below {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            raise ValueError(f"{location} exceeds {rule['maximum']}")


def _load_validated_profile(board_dir: Path, relative: str, schema_name: str) -> dict[str, Any]:
    profile_path = (board_dir / relative).resolve()
    profiles_root = (board_dir / "profiles").resolve()
    if profiles_root not in profile_path.parents:
        raise ValueError(f"Profile escapes the board profile directory: {relative}")
    schema = _load_json(profiles_root / "schemas" / schema_name)
    profile = _load_json(profile_path)
    _validate_schema(profile, schema, schema)
    return profile


def _parse_size(value: str) -> int:
    normalized = value.strip().lower()
    if normalized.startswith("0x"):
        return int(normalized, 16)
    multiplier = 1
    if normalized.endswith("k"):
        multiplier, normalized = 1024, normalized[:-1]
    elif normalized.endswith("m"):
        multiplier, normalized = 1024 * 1024, normalized[:-1]
    return int(normalized) * multiplier


def _validate_partition_layout(
    firmware_root: Path, product: dict[str, Any], flash_bytes: int
) -> None:
    partition = product["partition"]
    if not partition["custom"]:
        return
    path = (firmware_root / partition["file"]).resolve()
    if firmware_root.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"Partition file is missing or outside firmware root: {path}")
    ranges: list[tuple[int, int, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = [part.strip() for part in line.split(",")]
        if len(fields) < 5 or not fields[3]:
            continue
        start = _parse_size(fields[3])
        end = start + _parse_size(fields[4])
        if end > flash_bytes:
            raise ValueError(f"Partition {fields[0]} exceeds flash size")
        ranges.append((start, end, fields[0]))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:], strict=False):
        if current[0] < previous[1]:
            raise ValueError(f"Partitions overlap: {previous[2]} and {current[2]}")


def _validate_cross_profile(
    firmware_root: Path,
    board_dir: Path,
    hardware: dict[str, Any],
    display: dict[str, Any],
    product: dict[str, Any],
) -> None:
    versions = {hardware["schema_version"], display["schema_version"], product["schema_version"]}
    if versions != {1}:
        raise ValueError(f"Profile schema versions must all be 1, got {sorted(versions)}")

    used: dict[int, str] = {}
    for group_name, pins in hardware["pins"].items():
        for pin_name, pin in pins.items():
            if pin < 0:
                continue
            owner = f"{group_name}.{pin_name}"
            if pin in used:
                raise ValueError(f"GPIO {pin} conflicts between {used[pin]} and {owner}")
            used[pin] = owner

    physical = display["physical"]
    logical = display["logical"]
    swap_xy = display["transform"]["swap_xy"]
    expected = (
        (physical["height"], physical["width"])
        if swap_xy
        else (physical["width"], physical["height"])
    )
    if (logical["width"], logical["height"]) != expected:
        raise ValueError("Display logical size does not match physical size and swap_xy")

    if product["features"]["emote_engine"]:
        assets = display["emote_assets"]
        asset_dir = (board_dir / assets["directory"]).resolve()
        manifest_path = asset_dir / assets["manifest"]
        binary_path = asset_dir / assets["binary"]
        if board_dir.resolve() not in asset_dir.parents:
            raise ValueError("Emote asset directory escapes the board directory")
        manifest = _load_json(manifest_path)
        canvas = manifest.get("canvas", {})
        if (canvas.get("width"), canvas.get("height")) != (logical["width"], logical["height"]):
            raise ValueError("Emote canvas does not match DisplayProfile logical size")
        if not binary_path.is_file():
            raise ValueError(f"Emote binary is missing: {binary_path}")
        if binary_path.stat().st_size > product["partition"]["emote_max_bytes"]:
            raise ValueError("Emote binary exceeds ProductVariant partition limit")

    _validate_partition_layout(firmware_root, product, hardware["flash_bytes"])


def _gpio(value: int) -> str:
    return "GPIO_NUM_NC" if value < 0 else f"GPIO_NUM_{value}"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _quote_sdkconfig(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_sdkconfig(
    product: dict[str, Any],
    display: dict[str, Any],
    bootstrap_url: str | None,
) -> tuple[str, ...]:
    result = [
        "CONFIG_USE_HOTSPOT_WIFI_PROVISIONING=y",
        "CONFIG_USE_ESP_BLUFI_WIFI_PROVISIONING=n",
        f"CONFIG_SEND_WAKE_WORD_DATA={'y' if product['features']['send_wake_word_data'] else 'n'}",
    ]
    wake = product["wake"]
    if wake["enabled"]:
        result.extend(
            [
                "CONFIG_USE_CUSTOM_WAKE_WORD=y",
                f'CONFIG_CUSTOM_WAKE_WORD="{_quote_sdkconfig(wake["command"])}"',
                f'CONFIG_CUSTOM_WAKE_WORD_DISPLAY="{_quote_sdkconfig(wake["display_name"])}"',
                f"CONFIG_CUSTOM_WAKE_WORD_THRESHOLD={wake['threshold']}",
            ]
        )
        if wake["model"] == "multinet5-cn-q8":
            result.append("CONFIG_SR_MN_CN_MULTINET5_RECOGNITION_QUANT8=y")
    else:
        # Kconfig defaults ESP32-S3 builds to AFE WakeNet. Select the disabled
        # choice explicitly so a Profile with wake.enabled=false cannot retain
        # a model from a previous incremental build.
        result.extend(
            [
                "CONFIG_USE_AFE_WAKE_WORD=n",
                "CONFIG_USE_ESP_WAKE_WORD=n",
                "CONFIG_USE_CUSTOM_WAKE_WORD=n",
                "CONFIG_WAKE_WORD_DISABLED=y",
            ]
        )
    features = product["features"]
    if features["one_shot_conversation"]:
        result.append("CONFIG_HENSUN_ONE_SHOT_CONVERSATION=y")
    if features["emote_engine"]:
        result.extend(["CONFIG_USE_EMOTE_MESSAGE_STYLE=y", "CONFIG_FLASH_NONE_ASSETS=y"])
    if display["logical"]["width"] > display["logical"]["height"]:
        result.append("CONFIG_HENSUN_DISPLAY_LANDSCAPE=y")
    partition = product["partition"]
    if partition["custom"]:
        result.extend(
            [
                "CONFIG_PARTITION_TABLE_CUSTOM=y",
                f'CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="{_quote_sdkconfig(partition["file"])}"',
            ]
        )
    bootstrap = product["bootstrap"]
    url = bootstrap["fixed_url"]
    if bootstrap["strategy"] == "build-parameter":
        url = bootstrap_url or SAFE_BOOTSTRAP_PLACEHOLDER
    if not re.match(r"^https?://", url):
        raise ValueError("Bootstrap URL must be absolute HTTP(S)")
    result.append(f'CONFIG_OTA_URL="{_quote_sdkconfig(url.rstrip("/"))}"')
    return tuple(result)


def _render_header(
    hardware: dict[str, Any], display: dict[str, Any], product: dict[str, Any], digest: str
) -> str:
    audio_pins = hardware["pins"]["audio"]
    display_pins = hardware["pins"]["display"]
    camera_pins = hardware["pins"]["camera"]
    button_pins = hardware["pins"]["buttons"]
    transform = display["transform"]
    color_order = (
        "LCD_RGB_ELEMENT_ORDER_RGB"
        if display["color_order"] == "rgb"
        else "LCD_RGB_ELEMENT_ORDER_BGR"
    )
    macros = {
        "AUDIO_INPUT_SAMPLE_RATE": hardware["audio"]["input_sample_rate"],
        "AUDIO_OUTPUT_SAMPLE_RATE": hardware["audio"]["output_sample_rate"],
        "AUDIO_I2S_MIC_GPIO_WS": _gpio(audio_pins["mic_ws"]),
        "AUDIO_I2S_MIC_GPIO_SCK": _gpio(audio_pins["mic_sck"]),
        "AUDIO_I2S_MIC_GPIO_DIN": _gpio(audio_pins["mic_din"]),
        "AUDIO_I2S_SPK_GPIO_DOUT": _gpio(audio_pins["speaker_dout"]),
        "AUDIO_I2S_SPK_GPIO_BCLK": _gpio(audio_pins["speaker_bclk"]),
        "AUDIO_I2S_SPK_GPIO_LRCK": _gpio(audio_pins["speaker_lrck"]),
        "BOOT_BUTTON_GPIO": _gpio(button_pins["boot"]),
        "DISPLAY_BACKLIGHT_PIN": _gpio(display_pins["backlight"]),
        "DISPLAY_MOSI_PIN": _gpio(display_pins["mosi"]),
        "DISPLAY_CLK_PIN": _gpio(display_pins["clock"]),
        "DISPLAY_DC_PIN": _gpio(display_pins["dc"]),
        "DISPLAY_RST_PIN": _gpio(display_pins["reset"]),
        "DISPLAY_CS_PIN": _gpio(display_pins["cs"]),
        "DISPLAY_WIDTH": display["logical"]["width"],
        "DISPLAY_HEIGHT": display["logical"]["height"],
        "DISPLAY_MIRROR_X": _bool(transform["mirror_x"]),
        "DISPLAY_MIRROR_Y": _bool(transform["mirror_y"]),
        "DISPLAY_SWAP_XY": _bool(transform["swap_xy"]),
        "DISPLAY_INVERT_COLOR": _bool(display["invert_color"]),
        "DISPLAY_RGB_ORDER": color_order,
        "DISPLAY_OFFSET_X": display["offset"]["x"],
        "DISPLAY_OFFSET_Y": display["offset"]["y"],
        "DISPLAY_BACKLIGHT_OUTPUT_INVERT": "false",
        "DISPLAY_SPI_HOST": f"SPI{display['spi']['host']}_HOST",
        "DISPLAY_SPI_MODE": display["spi"]["mode"],
        "DISPLAY_SPI_FREQUENCY_HZ": display["spi"]["frequency_hz"],
        "DISPLAY_SPI_QUEUE_DEPTH": display["spi"]["queue_depth"],
        "CAMERA_XCLK_HZ": hardware["camera"]["xclk_hz"],
    }
    for name, pin in camera_pins.items():
        macros[f"CAMERA_PIN_{name.upper()}"] = _gpio(pin)
    lines = [
        "// Generated by scripts/profile_codegen.py. Do not edit.",
        "#pragma once",
        "#include <driver/gpio.h>",
        "#include <driver/spi_master.h>",
        "#include <esp_lcd_types.h>",
        f'#define HENSUN_HARDWARE_PROFILE_ID "{hardware["id"]}"',
        f'#define HENSUN_DISPLAY_PROFILE_ID "{display["id"]}"',
        f'#define HENSUN_PRODUCT_VARIANT_ID "{product["id"]}"',
        "#define HENSUN_PROFILE_SCHEMA_VERSION 1",
        f'#define HENSUN_PROFILE_SHA256 "{digest}"',
    ]
    lines.extend(f"#define {name} {value}" for name, value in macros.items())
    return "\n".join(lines) + "\n"


def render_profile_bundle(
    firmware_root: Path,
    board_dir: Path,
    build: dict[str, Any],
    *,
    bootstrap_url: str | None = None,
) -> RenderedProfile:
    bundle = build.get("profile_bundle")
    if not isinstance(bundle, dict):
        raise ValueError(f"Build {build.get('name')!r} has no profile_bundle")
    expected = {"hardware", "display", "product"}
    if set(bundle) != expected:
        raise ValueError(f"profile_bundle must contain exactly {sorted(expected)}")
    hardware = _load_validated_profile(
        board_dir, bundle["hardware"], "hardware-profile.schema.json"
    )
    display = _load_validated_profile(board_dir, bundle["display"], "display-profile.schema.json")
    product = _load_validated_profile(board_dir, bundle["product"], "product-variant.schema.json")
    _validate_cross_profile(firmware_root, board_dir, hardware, display, product)
    canonical = json.dumps(
        {"hardware": hardware, "display": display, "product": product},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    asset = display["emote_assets"]
    cmake = "\n".join(
        [
            "# Generated by scripts/profile_codegen.py. Do not edit.",
            "set(HENSUN_PROFILE_GENERATED TRUE)",
            f'set(HENSUN_HARDWARE_PROFILE_ID "{hardware["id"]}")',
            f'set(HENSUN_DISPLAY_PROFILE_ID "{display["id"]}")',
            f'set(HENSUN_PRODUCT_VARIANT_ID "{product["id"]}")',
            f'set(HENSUN_PROFILE_SHA256 "{digest}")',
            "set(HENSUN_EMOTE_ASSET_DIR "
            f'"${{CMAKE_CURRENT_SOURCE_DIR}}/boards/hensun/'
            f'hensun-cam-pilot-v1/{asset["directory"]}")',
            f'set(HENSUN_EMOTE_ASSET_BIN "${{HENSUN_EMOTE_ASSET_DIR}}/{asset["binary"]}")',
            f"set(HENSUN_DISPLAY_WIDTH {display['logical']['width']})",
            f"set(HENSUN_DISPLAY_HEIGHT {display['logical']['height']})",
            "",
        ]
    )
    metadata = (
        json.dumps(
            {
                "schema_version": 1,
                "build_name": build["name"],
                "hardware_profile_id": hardware["id"],
                "display_profile_id": display["id"],
                "product_variant_id": product["id"],
                "profile_sha256": digest,
                "emote_asset_directory": asset["directory"],
                "emote_binary": asset["binary"],
                "model_max_bytes": product["partition"]["model_max_bytes"],
                "emote_max_bytes": product["partition"]["emote_max_bytes"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return RenderedProfile(
        sdkconfig=_render_sdkconfig(product, display, bootstrap_url),
        header=_render_header(hardware, display, product, digest),
        cmake=cmake,
        metadata=metadata,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("board_dir", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--firmware-root", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--bootstrap-url")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    board_dir = args.board_dir.resolve()
    firmware_root = (args.firmware_root or board_dir.parents[4]).resolve()
    config = _load_json(board_dir / "config.json")
    build = next((item for item in config["builds"] if item["name"] == args.name), None)
    if build is None:
        raise ValueError(f"Unknown build profile: {args.name}")
    rendered = render_profile_bundle(
        firmware_root, board_dir, build, bootstrap_url=args.bootstrap_url
    )
    output_dir = (args.output_dir or firmware_root / "build/generated").resolve()
    rendered.write(output_dir, check=args.check)
    print(f"PROFILE OK: {args.name} -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
