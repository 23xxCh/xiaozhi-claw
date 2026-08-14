#!/usr/bin/env python3
"""Generate the original Hensun black-and-white emote lab GIF sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw


BOARD = Path(__file__).resolve().parents[1]
ASSET_ROOT = BOARD / "emote_lab"
SOURCE_SPEC = ASSET_ROOT / "source/hensun_emote_motion_spec.json"
GIF_ROOT = ASSET_ROOT / "gifs"
MANIFEST = ASSET_ROOT / "manifest.json"
CONTACT_SHEET = ASSET_ROOT / "hensun_emote_lab_v1_contact_sheet.png"
ANIMATED_CONTACT_SHEET = ASSET_ROOT / "hensun_emote_lab_v1_motion_preview.gif"
SCALE = 4


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def ease_in_out(amount: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp(amount))


def ease_out_back(amount: float) -> float:
    amount = clamp(amount)
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (amount - 1.0) ** 3 + c1 * (amount - 1.0) ** 2


def stage(frame: int, total: int, loop_start: int, loop_end: int) -> tuple[float, float]:
    if frame < loop_start:
        active = ease_out_back((frame + 1) / loop_start)
        phase = 0.0
    elif frame < loop_end:
        active = 1.0
        phase = (frame - loop_start) / max(1, loop_end - loop_start)
    else:
        active = 1.0 - ease_in_out((frame - loop_end + 1) / max(1, total - loop_end))
        phase = 1.0
    return clamp(active, 0.0, 1.08), phase


def color_with_alpha(rgb: tuple[int, int, int], alpha: float) -> tuple[int, int, int, int]:
    return (*rgb, int(round(255 * clamp(alpha))))


def pill(
    image: Image.Image,
    center: tuple[float, float],
    size: tuple[float, float],
    angle: float = 0.0,
    color: tuple[int, int, int] = (247, 247, 242),
    alpha: float = 1.0,
) -> None:
    width = max(2, int(round(size[0] * SCALE)))
    height = max(2, int(round(size[1] * SCALE)))
    shape = Image.new("RGBA", (width + 12 * SCALE, height + 12 * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shape)
    pad = 6 * SCALE
    draw.rounded_rectangle(
        (pad, pad, pad + width, pad + height),
        radius=min(width, height) // 2,
        fill=color_with_alpha(color, alpha),
    )
    if angle:
        shape = shape.rotate(angle, Image.Resampling.BICUBIC, expand=True)
    x = int(round(center[0] * SCALE - shape.width / 2))
    y = int(round(center[1] * SCALE - shape.height / 2))
    image.alpha_composite(shape, (x, y))


def ellipse(
    image: Image.Image,
    center: tuple[float, float],
    size: tuple[float, float],
    color: tuple[int, int, int] = (247, 247, 242),
    alpha: float = 1.0,
) -> None:
    draw = ImageDraw.Draw(image)
    half_w = size[0] * SCALE / 2
    half_h = size[1] * SCALE / 2
    cx = center[0] * SCALE
    cy = center[1] * SCALE
    draw.ellipse(
        (cx - half_w, cy - half_h, cx + half_w, cy + half_h),
        fill=color_with_alpha(color, alpha),
    )


def superellipse(
    image: Image.Image,
    center: tuple[float, float],
    size: tuple[float, float],
    exponent: float = 2.6,
    angle: float = 0.0,
    color: tuple[int, int, int] = (247, 247, 242),
    alpha: float = 1.0,
) -> None:
    """Draw a soft, organic eye silhouette instead of a generic oval."""
    cx, cy = center[0] * SCALE, center[1] * SCALE
    half_w, half_h = size[0] * SCALE / 2, size[1] * SCALE / 2
    rotation = math.radians(angle)
    points = []
    for index in range(96):
        theta = math.tau * index / 96
        cosine, sine = math.cos(theta), math.sin(theta)
        x = half_w * math.copysign(abs(cosine) ** (2.0 / exponent), cosine)
        y = half_h * math.copysign(abs(sine) ** (2.0 / exponent), sine)
        points.append(
            (
                cx + x * math.cos(rotation) - y * math.sin(rotation),
                cy + x * math.sin(rotation) + y * math.cos(rotation),
            )
        )
    ImageDraw.Draw(image).polygon(points, fill=color_with_alpha(color, alpha))


def arc_stroke(
    image: Image.Image,
    center: tuple[float, float],
    size: tuple[float, float],
    start: float,
    end: float,
    width: float,
    angle: float = 0.0,
    color: tuple[int, int, int] = (247, 247, 242),
    alpha: float = 1.0,
) -> None:
    pad = int((width + 6) * SCALE)
    layer_size = (
        max(8, int(size[0] * SCALE) + pad * 2),
        max(8, int(size[1] * SCALE) + pad * 2),
    )
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.arc(
        (pad, pad, layer_size[0] - pad, layer_size[1] - pad),
        start=start,
        end=end,
        fill=color_with_alpha(color, alpha),
        width=max(2, int(width * SCALE)),
    )
    if angle:
        layer = layer.rotate(angle, Image.Resampling.BICUBIC, expand=True)
    image.alpha_composite(
        layer,
        (
            int(center[0] * SCALE - layer.width / 2),
            int(center[1] * SCALE - layer.height / 2),
        ),
    )


def eye_blob(
    image: Image.Image,
    center: tuple[float, float],
    size: tuple[float, float],
    pupil_offset: tuple[float, float],
    pupil_size: tuple[float, float],
    *,
    angle: float = 0.0,
    lid: float = 0.0,
    alpha: float = 1.0,
) -> None:
    superellipse(image, center, size, exponent=2.65, angle=angle, alpha=alpha)
    if lid > 0:
        # A curved black cut creates an eyelid with much more character than a
        # separate generic eyebrow. It stays inside the white eye silhouette.
        ellipse(
            image,
            (center[0], center[1] - size[1] * (0.55 - lid * 0.28)),
            (size[0] * 1.18, size[1] * 0.42),
            color=(0, 0, 0),
        )
    ellipse(
        image,
        (center[0] + pupil_offset[0], center[1] + pupil_offset[1]),
        pupil_size,
        color=(0, 0, 0),
    )


def cheek_dashes(image: Image.Image, y: float, alpha: float, spread: float = 0.0) -> None:
    pill(image, (50 - spread, y), (20, 6), 16, alpha=alpha * 0.88)
    pill(image, (190 + spread, y), (20, 6), -16, alpha=alpha * 0.88)


def crescent(
    image: Image.Image,
    center: tuple[float, float],
    size: tuple[float, float],
    lift: float,
    alpha: float,
) -> None:
    ellipse(image, center, size, alpha=alpha)
    ellipse(
        image,
        (center[0], center[1] + lift),
        (size[0] * 0.86, size[1] * 0.82),
        color=(0, 0, 0),
        alpha=1.0,
    )


def smile_arc(image: Image.Image, center: tuple[float, float], size: tuple[float, float], alpha: float) -> None:
    outer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(outer)
    cx, cy = center[0] * SCALE, center[1] * SCALE
    hw, hh = size[0] * SCALE / 2, size[1] * SCALE / 2
    draw.arc(
        (cx - hw, cy - hh, cx + hw, cy + hh),
        start=18,
        end=162,
        fill=color_with_alpha((247, 247, 242), alpha),
        width=max(2, int(7 * SCALE)),
    )
    image.alpha_composite(outer)


def draw_bridge(image: Image.Image, weight: float, y_offset: float = 0.0) -> None:
    if weight <= 0:
        return
    pill(image, (76, 150 + y_offset), (48, 10), -2, alpha=weight)
    pill(image, (164, 150 + y_offset), (48, 10), 2, alpha=weight)


def draw_idle(image: Image.Image, active: float, phase: float) -> None:
    breathe = math.sin(phase * math.tau)
    blink = clamp(1.0 - abs(phase - 0.64) / 0.055)
    eye_h = lerp(10, 88 + breathe * 2, active) * (1.0 - 0.84 * blink)
    eye_w = lerp(48, 66, active) + blink * 3
    gaze = math.sin(phase * math.pi) * 3.5
    y = lerp(150, 145 + breathe * 1.2, active)
    for side, x in ((-1, 76), (1, 164)):
        eye_blob(
            image,
            (x, y),
            (eye_w, max(9, eye_h)),
            (side * -2 + gaze, 11),
            (18, max(10, min(32, eye_h * 0.38))),
            angle=side * 1.5,
            lid=0.16,
            alpha=min(1.0, active + 0.16),
        )
    cheek_dashes(image, 192 + breathe, active)
    arc_stroke(image, (120, 193 + breathe), (36, 17), 20, 160, 5, alpha=active * 0.9)


def draw_listening(image: Image.Image, active: float, phase: float) -> None:
    pulse = 0.5 - 0.5 * math.cos(phase * math.tau)
    inward = 4.5 * pulse
    eye_w = lerp(48, 70 + pulse * 2, active)
    eye_h = lerp(10, 96 - pulse * 3, active)
    for side, x in ((-1, 72 + inward), (1, 168 - inward)):
        eye_blob(
            image,
            (x, 146),
            (eye_w, eye_h),
            (-side * 8, 4),
            (17, 31),
            angle=side * 1.2,
            lid=0.08,
            alpha=min(1.0, active + 0.18),
        )
        arc_stroke(image, (x, 93 - pulse * 3), (42, 20), 205, 335, 6, angle=side * 5, alpha=active)
    cheek_dashes(image, 197, active, pulse * 2)
    ellipse(image, (120, 200), (10 + pulse * 3, 13 + pulse * 3), alpha=active * 0.94)


def draw_thinking(image: Image.Image, active: float, phase: float) -> None:
    drift = math.sin(phase * math.tau) * 5
    open_h = lerp(10, 88, active)
    eye_blob(
        image,
        (76 + drift * 0.35, 148),
        (70, open_h),
        (9 + drift * 0.35, -14),
        (19, 29),
        angle=-4,
        lid=0.18,
        alpha=min(1.0, active + 0.16),
    )
    arc_stroke(image, (164, 147 - drift * 0.18), (67, 31), 200, 338, 10, angle=-8, alpha=active)
    arc_stroke(image, (73, 92), (48, 22), 205, 335, 6, angle=11, alpha=active)
    arc_stroke(image, (166, 103), (41, 19), 205, 335, 6, angle=-9, alpha=active * 0.9)
    cheek_dashes(image, 198, active)
    pill(image, (122, 199), (18, 7), -10, alpha=active * 0.86)


def draw_speaking(image: Image.Image, active: float, phase: float) -> None:
    beat = 0.5 - 0.5 * math.cos(phase * math.tau * 2)
    bounce = math.sin(phase * math.tau * 2) * 2.2
    for side, x in ((-1, 76), (1, 164)):
        eye_blob(
            image,
            (x, 137 + bounce * side),
            (68 + beat * 2, lerp(10, 64 - beat * 8, active)),
            (-side * 10, 3),
            (18, 25),
            angle=side * (2 + beat * 2),
            lid=0.12,
            alpha=min(1.0, active + 0.17),
        )
    mouth_w = lerp(14, 58 - beat * 10, active)
    mouth_h = lerp(8, 49 + beat * 11, active)
    ellipse(image, (120, 205), (mouth_w, mouth_h), alpha=active)
    ellipse(image, (120, 201 - beat * 2), (mouth_w * 0.62, mouth_h * 0.55), color=(0, 0, 0))
    cheek_dashes(image, 190 + bounce, active)


def draw_happy(image: Image.Image, active: float, phase: float) -> None:
    bounce = math.sin(phase * math.tau) * 3.0
    squeeze = 1.0 + math.sin(phase * math.tau) * 0.05
    for x in (75, 165):
        arc_stroke(
            image,
            (x, 150 + bounce),
            (68 * squeeze, 49 / squeeze),
            198,
            342,
            11,
            alpha=active,
        )
    cheek_dashes(image, 194 + bounce, active, 2)
    arc_stroke(image, (120, 194 + bounce), (67, 37), 18, 162, 8, alpha=active)


def draw_caring(image: Image.Image, active: float, phase: float) -> None:
    breathe = math.sin(phase * math.tau) * 1.8
    settle = math.sin(phase * math.tau * 0.5) * 1.5
    eye_h = lerp(10, 88 + breathe, active)
    for side, x in ((-1, 76), (1, 164)):
        eye_blob(
            image,
            (x, 151 + breathe),
            (68, eye_h),
            (-side * 10, 15 + settle),
            (20, 28),
            angle=side * -3,
            lid=0.24,
            alpha=min(1.0, active + 0.16),
        )
        arc_stroke(image, (x, 96 + breathe), (48, 23), 205, 335, 6, angle=side * 10, alpha=active)
    cheek_dashes(image, 201 + breathe, active)
    arc_stroke(image, (120, 201 + breathe), (38, 19), 18, 162, 5, alpha=active * 0.92)


DRAWERS: dict[str, Callable[[Image.Image, float, float], None]] = {
    "idle": draw_idle,
    "listening": draw_listening,
    "thinking": draw_thinking,
    "speaking": draw_speaking,
    "happy": draw_happy,
    "caring": draw_caring,
}


def render_frame(name: str, frame: int, animation: dict) -> Image.Image:
    total = animation["frames"]
    loop_start = animation["loop_start_frame"]
    loop_end = animation["loop_end_frame"]
    active, phase = stage(frame, total, loop_start, loop_end)
    canvas = Image.new("RGBA", (240 * SCALE, 320 * SCALE), (0, 0, 0, 255))
    draw_bridge(canvas, clamp(1.0 - active))
    DRAWERS[name](canvas, active, phase)
    output = canvas.convert("RGB").resize((240, 320), Image.Resampling.LANCZOS)
    # GIF writers are allowed to merge identical consecutive frames. The packer
    # derives segment timing from a constant-rate frame stream, so preserve each
    # 50 ms tick with one imperceptible grayscale timing pixel under the bezel.
    output.putpixel((208 + frame % 31, 319), (247, 247, 242))
    return output


def save_gif(path: Path, frames: list[Image.Image]) -> None:
    adaptive = [frame.quantize(colors=32, method=Image.Quantize.MEDIANCUT) for frame in frames]
    adaptive[0].save(
        path,
        save_all=True,
        append_images=adaptive[1:],
        duration=50,
        loop=0,
        optimize=False,
        disposal=2,
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_assets(check: bool) -> int:
    spec = json.loads(SOURCE_SPEC.read_text(encoding="utf-8"))
    GIF_ROOT.mkdir(parents=True, exist_ok=True)
    manifest_animations = {}
    contact_frames = []
    animation_frames: dict[str, list[Image.Image]] = {}

    for name, animation in spec["animations"].items():
        frames = [render_frame(name, index, animation) for index in range(animation["frames"])]
        animation_frames[name] = frames
        path = GIF_ROOT / animation["export_file"]
        if check:
            if not path.is_file():
                raise SystemExit(f"missing generated GIF: {path}")
            temporary = path.with_suffix(".check.gif")
            save_gif(temporary, frames)
            try:
                if temporary.read_bytes() != path.read_bytes():
                    raise SystemExit(f"generated GIF is stale: {path}")
            finally:
                temporary.unlink(missing_ok=True)
        else:
            save_gif(path, frames)
        contact_frames.append(frames[animation["loop_start_frame"]])
        manifest_animations[name] = {
            "file": animation["export_file"],
            "frames": animation["frames"],
            "loop_start_frame": animation["loop_start_frame"],
            "loop_end_frame": animation["loop_end_frame"],
            "sha256": sha256(path),
        }

    if not check:
        sheet = Image.new("RGB", (240 * 3, 320 * 2), (0, 0, 0))
        for index, frame in enumerate(contact_frames):
            sheet.paste(frame, ((index % 3) * 240, (index // 3) * 320))
        sheet.save(CONTACT_SHEET, optimize=True)

        preview_frames = []
        animation_names = list(spec["animations"])
        for frame_index in range(48):
            preview = Image.new("RGB", (240 * 3, 320 * 2), (0, 0, 0))
            for index, name in enumerate(animation_names):
                frames = animation_frames[name]
                preview.paste(
                    frames[frame_index % len(frames)],
                    ((index % 3) * 240, (index // 3) * 320),
                )
            preview_frames.append(preview)
        save_gif(ANIMATED_CONTACT_SHEET, preview_frames)

    existing_pack = ASSET_ROOT / "hensun_emote_lab_v1.bin"
    manifest = {
        "asset_set": spec["asset_set"],
        "source_spec": str(SOURCE_SPEC.relative_to(ASSET_ROOT)).replace("\\", "/"),
        "source_spec_sha256": sha256(SOURCE_SPEC),
        "canvas": spec["canvas"],
        "palette": spec["palette"],
        "animations": manifest_animations,
        "packer": {
            "name": "ESP Emote GFX Packer NEXT",
            "url": "https://emote-gfx-gen-tool-dev.pages.dev/",
            "player_commit": "7139b46c6616d466ff153cb9d2ddf63661434f22"
        },
        "pack": {
            "file": existing_pack.name,
            "animation_count": 6,
            "asset_count": 7,
            "sha256": sha256(existing_pack) if existing_pack.is_file() else "PENDING_PACKER_EXPORT"
        },
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if check:
        if not MANIFEST.is_file() or MANIFEST.read_text(encoding="utf-8") != manifest_text:
            raise SystemExit(f"manifest is stale: {MANIFEST}")
    else:
        MANIFEST.write_text(manifest_text, encoding="utf-8", newline="\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify committed outputs")
    args = parser.parse_args()
    return build_assets(args.check)


if __name__ == "__main__":
    raise SystemExit(main())
