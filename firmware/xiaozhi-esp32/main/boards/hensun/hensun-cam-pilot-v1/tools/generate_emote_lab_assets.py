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
DIALOGUE_RUNTIME_MANIFEST = ASSET_ROOT / "dialogue_runtime/manifest.json"
CONTACT_SHEET = ASSET_ROOT / "hensun_emote_lab_v1_contact_sheet.png"
ANIMATED_CONTACT_SHEET = ASSET_ROOT / "hensun_emote_lab_v1_motion_preview.gif"
SCALE = 4
BASE_WIDTH = 240
BASE_HEIGHT = 320
CANVAS_WIDTH = 320
CANVAS_HEIGHT = 240
X_SCALE = CANVAS_WIDTH / BASE_WIDTH
Y_SCALE = CANVAS_HEIGHT / BASE_HEIGHT
STROKE_SCALE = math.sqrt(X_SCALE * Y_SCALE)
PRIMARY_ANIMATIONS = (
    "sleep",
    "wake",
    "idle",
    "listening",
    "thinking",
    "speaking",
    "happy",
    "caring",
    "curious",
    "surprised",
    "confused",
    "alert",
)
CONTACT_COLUMNS = 3


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


def scale_point(center: tuple[float, float]) -> tuple[float, float]:
    return center[0] * X_SCALE, center[1] * Y_SCALE


def scale_size(size: tuple[float, float]) -> tuple[float, float]:
    return size[0] * X_SCALE, size[1] * Y_SCALE


def pill(
    image: Image.Image,
    center: tuple[float, float],
    size: tuple[float, float],
    angle: float = 0.0,
    color: tuple[int, int, int] = (247, 247, 242),
    alpha: float = 1.0,
) -> None:
    scaled_size = scale_size(size)
    scaled_center = scale_point(center)
    width = max(2, int(round(scaled_size[0] * SCALE)))
    height = max(2, int(round(scaled_size[1] * SCALE)))
    pad = max(2, int(round(6 * SCALE * STROKE_SCALE)))
    shape = Image.new("RGBA", (width + 2 * pad, height + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shape)
    draw.rounded_rectangle(
        (pad, pad, pad + width, pad + height),
        radius=min(width, height) // 2,
        fill=color_with_alpha(color, alpha),
    )
    if angle:
        shape = shape.rotate(angle, Image.Resampling.BICUBIC, expand=True)
    x = int(round(scaled_center[0] * SCALE - shape.width / 2))
    y = int(round(scaled_center[1] * SCALE - shape.height / 2))
    image.alpha_composite(shape, (x, y))


def ellipse(
    image: Image.Image,
    center: tuple[float, float],
    size: tuple[float, float],
    color: tuple[int, int, int] = (247, 247, 242),
    alpha: float = 1.0,
) -> None:
    draw = ImageDraw.Draw(image)
    scaled_size = scale_size(size)
    scaled_center = scale_point(center)
    half_w = scaled_size[0] * SCALE / 2
    half_h = scaled_size[1] * SCALE / 2
    cx = scaled_center[0] * SCALE
    cy = scaled_center[1] * SCALE
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
    scaled_center = scale_point(center)
    scaled_size = scale_size(size)
    cx, cy = scaled_center[0] * SCALE, scaled_center[1] * SCALE
    half_w, half_h = scaled_size[0] * SCALE / 2, scaled_size[1] * SCALE / 2
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
    scaled_size = scale_size(size)
    scaled_center = scale_point(center)
    scaled_width = width * STROKE_SCALE
    pad = int((scaled_width + 6 * STROKE_SCALE) * SCALE)
    layer_size = (
        max(8, int(scaled_size[0] * SCALE) + pad * 2),
        max(8, int(scaled_size[1] * SCALE) + pad * 2),
    )
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.arc(
        (pad, pad, layer_size[0] - pad, layer_size[1] - pad),
        start=start,
        end=end,
        fill=color_with_alpha(color, alpha),
        width=max(2, int(scaled_width * SCALE)),
    )
    if angle:
        layer = layer.rotate(angle, Image.Resampling.BICUBIC, expand=True)
    image.alpha_composite(
        layer,
        (
            int(scaled_center[0] * SCALE - layer.width / 2),
            int(scaled_center[1] * SCALE - layer.height / 2),
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
    scaled_center = scale_point(center)
    scaled_size = scale_size(size)
    cx, cy = scaled_center[0] * SCALE, scaled_center[1] * SCALE
    hw, hh = scaled_size[0] * SCALE / 2, scaled_size[1] * SCALE / 2
    draw.arc(
        (cx - hw, cy - hh, cx + hw, cy + hh),
        start=18,
        end=162,
        fill=color_with_alpha((247, 247, 242), alpha),
        width=max(2, int(7 * STROKE_SCALE * SCALE)),
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


def draw_speaking(
    image: Image.Image, active: float, phase: float, speech_level: int = 2
) -> None:
    beat = 0.5 - 0.5 * math.cos(phase * math.tau * 2)
    amplitude = (0.16, 0.43, 0.72, 1.0)[max(0, min(3, speech_level))]
    bounce = math.sin(phase * math.tau * 2) * (0.8 + amplitude * 2.0)
    for side, x in ((-1, 76), (1, 164)):
        eye_blob(
            image,
            (x, 137 + bounce * side),
            (68 + beat * 2 * amplitude, lerp(10, 66 - beat * 9 * amplitude, active)),
            (-side * 10, 3),
            (18, 25),
            angle=side * (2 + beat * 2),
            lid=0.12,
            alpha=min(1.0, active + 0.17),
        )
    mouth_w = lerp(14, 30 + amplitude * 34 - beat * (4 + amplitude * 7), active)
    mouth_h = lerp(8, 10 + amplitude * 57 + beat * (3 + amplitude * 10), active)
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


def draw_wake(image: Image.Image, active: float, phase: float) -> None:
    opening = ease_out_back(clamp(phase * 1.75 + 0.16))
    sparkle = 0.5 - 0.5 * math.cos(phase * math.tau)
    for side, x in ((-1, 76), (1, 164)):
        eye_blob(
            image,
            (x, 147 - opening * 4),
            (lerp(50, 70, active), lerp(10, 94, active) * opening),
            (-side * 4, 4),
            (18, max(8, 30 * opening)),
            angle=side * 1.5,
            lid=0.04,
            alpha=min(1.0, active + 0.18),
        )
        arc_stroke(image, (x, 94 - opening * 4), (43, 20), 205, 335, 6, alpha=active)
    smile_arc(image, (120, 195), (42, 22), active * 0.85)
    ellipse(image, (198, 100 - sparkle * 5), (5 + sparkle * 2, 5 + sparkle * 2), alpha=active)


def draw_curious(image: Image.Image, active: float, phase: float) -> None:
    glance = math.sin(phase * math.tau) * 6
    eye_blob(
        image,
        (75, 147),
        (68, lerp(10, 88, active)),
        (10 + glance, -9),
        (19, 29),
        angle=-5,
        lid=0.11,
        alpha=min(1.0, active + 0.18),
    )
    eye_blob(
        image,
        (165, 151),
        (66, lerp(10, 76, active)),
        (7 + glance * 0.45, 7),
        (18, 25),
        angle=4,
        lid=0.18,
        alpha=min(1.0, active + 0.18),
    )
    arc_stroke(image, (72, 92), (49, 23), 205, 335, 7, angle=10, alpha=active)
    pill(image, (120, 200), (13, 10), alpha=active * 0.9)
    ellipse(image, (120, 200), (6, 5), color=(0, 0, 0))


def draw_surprised(image: Image.Image, active: float, phase: float) -> None:
    pulse = 0.5 - 0.5 * math.cos(phase * math.tau)
    for side, x in ((-1, 75), (1, 165)):
        eye_blob(
            image,
            (x, 146 - pulse * 2),
            (70 + pulse * 2, lerp(10, 100 + pulse * 4, active)),
            (-side * 1, 4),
            (18, 34),
            angle=side * 1.0,
            lid=0.0,
            alpha=min(1.0, active + 0.18),
        )
        arc_stroke(image, (x, 88 - pulse * 2), (50, 23), 205, 335, 7, angle=side * 5, alpha=active)
    ellipse(image, (120, 202), (23 + pulse * 2, 31 + pulse * 3), alpha=active)
    ellipse(image, (120, 202), (12 + pulse, 18 + pulse), color=(0, 0, 0))


def draw_confused(image: Image.Image, active: float, phase: float) -> None:
    wobble = math.sin(phase * math.tau) * 4
    eye_blob(
        image,
        (76, 148 + wobble * 0.35),
        (69, lerp(10, 83, active)),
        (7, 7),
        (19, 27),
        angle=-7,
        lid=0.25,
        alpha=min(1.0, active + 0.16),
    )
    eye_blob(
        image,
        (165, 147 - wobble * 0.35),
        (68, lerp(10, 80, active)),
        (-9, -4),
        (18, 26),
        angle=7,
        lid=0.08,
        alpha=min(1.0, active + 0.16),
    )
    arc_stroke(image, (73, 92), (48, 22), 205, 335, 7, angle=13, alpha=active)
    arc_stroke(image, (168, 99), (42, 19), 205, 335, 6, angle=-14, alpha=active)
    pill(image, (120, 200), (23, 7), -9 + wobble * 0.3, alpha=active * 0.9)


def draw_alert(image: Image.Image, active: float, phase: float) -> None:
    beat = 0.5 - 0.5 * math.cos(phase * math.tau * 2)
    for side, x in ((-1, 76), (1, 164)):
        eye_blob(
            image,
            (x, 151),
            (70, lerp(9, 60 - beat * 6, active)),
            (-side * 7, 2),
            (18, 21),
            angle=side * 5,
            lid=0.4,
            alpha=min(1.0, active + 0.17),
        )
    pill(image, (120, 201), (31, 7), 0, alpha=active)
    pill(image, (204, 104 - beat * 3), (7, 27), 0, alpha=active)
    ellipse(image, (204, 126 - beat * 3), (8, 8), alpha=active)


def draw_sleep(image: Image.Image, active: float, phase: float) -> None:
    breathe = math.sin(phase * math.tau) * 1.5
    drift = (phase - 0.5) * 8
    for side, x in ((-1, 76), (1, 164)):
        arc_stroke(
            image,
            (x, 151 + breathe),
            (68, 30),
            198,
            342,
            8,
            angle=side * 1.5,
            alpha=active,
        )
    arc_stroke(image, (120, 196 + breathe), (34, 16), 18, 162, 5, alpha=active * 0.82)
    z_alpha = active * (0.35 + 0.55 * (0.5 - 0.5 * math.cos(phase * math.tau)))
    for index, (x, y, size) in enumerate(((204, 95, 1.0), (220, 76, 0.72))):
        offset = drift * (index + 1) * 0.35
        pill(image, (x + offset, y), (18 * size, 4 * size), alpha=z_alpha)
        pill(image, (x + offset, y + 11 * size), (18 * size, 4 * size), alpha=z_alpha)
        pill(image, (x + offset, y + 5.5 * size), (4 * size, 13 * size), 38, alpha=z_alpha)


DRAWERS: dict[str, Callable[[Image.Image, float, float], None]] = {
    "idle": draw_idle,
    "listening": draw_listening,
    "thinking": draw_thinking,
    "speaking": draw_speaking,
    "happy": draw_happy,
    "caring": draw_caring,
    "wake": draw_wake,
    "curious": draw_curious,
    "surprised": draw_surprised,
    "confused": draw_confused,
    "alert": draw_alert,
    "sleep": draw_sleep,
}


def render_frame(name: str, frame: int, animation: dict) -> Image.Image:
    total = animation["frames"]
    loop_start = animation["loop_start_frame"]
    loop_end = animation["loop_end_frame"]
    active, phase = stage(frame, total, loop_start, loop_end)
    canvas = Image.new("RGBA", (CANVAS_WIDTH * SCALE, CANVAS_HEIGHT * SCALE), (0, 0, 0, 255))
    draw_bridge(canvas, clamp(1.0 - active))
    renderer = animation.get("renderer", name)
    if renderer == "speaking":
        draw_speaking(canvas, active, phase, int(animation.get("speech_level", 2)))
    else:
        DRAWERS[renderer](canvas, active, phase)
    output = canvas.convert("RGB").resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.Resampling.LANCZOS)
    # GIF writers are allowed to merge identical consecutive frames. The packer
    # derives segment timing from a constant-rate frame stream, so preserve each
    # 50 ms tick with one imperceptible grayscale timing pixel under the bezel.
    output.putpixel((CANVAS_WIDTH - 32 + frame % 31, CANVAS_HEIGHT - 1), (247, 247, 242))
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


def normalized_text_sha256(path: Path) -> str:
    """Hash text inputs as LF so the manifest is portable across Git clients."""
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_assets(check: bool) -> int:
    spec = json.loads(SOURCE_SPEC.read_text(encoding="utf-8"))
    if spec["canvas"] != {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT, "fps": 20}:
        raise SystemExit("source spec canvas must match the Hensun landscape panel")
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
        if name in PRIMARY_ANIMATIONS:
            contact_frames.append(frames[animation["loop_start_frame"]])
        manifest_animations[name] = {
            "file": animation["export_file"],
            "frames": animation["frames"],
            "loop_start_frame": animation["loop_start_frame"],
            "loop_end_frame": animation["loop_end_frame"],
            "sha256": sha256(path),
        }

    if not check:
        contact_rows = math.ceil(len(contact_frames) / CONTACT_COLUMNS)
        sheet = Image.new(
            "RGB", (CANVAS_WIDTH * CONTACT_COLUMNS, CANVAS_HEIGHT * contact_rows), (0, 0, 0)
        )
        for index, frame in enumerate(contact_frames):
            sheet.paste(
                frame,
                ((index % CONTACT_COLUMNS) * CANVAS_WIDTH, (index // CONTACT_COLUMNS) * CANVAS_HEIGHT),
            )
        sheet.save(CONTACT_SHEET, optimize=True)

        preview_frames = []
        animation_names = list(PRIMARY_ANIMATIONS)
        for frame_index in range(48):
            preview = Image.new(
                "RGB",
                (CANVAS_WIDTH * CONTACT_COLUMNS, CANVAS_HEIGHT * contact_rows),
                (0, 0, 0),
            )
            for index, name in enumerate(animation_names):
                frames = animation_frames[name]
                preview.paste(
                    frames[frame_index % len(frames)],
                    (
                        (index % CONTACT_COLUMNS) * CANVAS_WIDTH,
                        (index // CONTACT_COLUMNS) * CANVAS_HEIGHT,
                    ),
                )
            preview_frames.append(preview)
        save_gif(ANIMATED_CONTACT_SHEET, preview_frames)

    existing_pack = ASSET_ROOT / "hensun_emote_lab_v1.bin"
    dialogue_runtime_count = 0
    if DIALOGUE_RUNTIME_MANIFEST.is_file():
        dialogue_runtime = json.loads(DIALOGUE_RUNTIME_MANIFEST.read_text(encoding="utf-8"))
        dialogue_runtime_count = len(dialogue_runtime["expressions"])
    packed_animation_count = len(spec["animations"]) + dialogue_runtime_count

    manifest = {
        "asset_set": spec["asset_set"],
        "source_spec": str(SOURCE_SPEC.relative_to(ASSET_ROOT)).replace("\\", "/"),
        "source_spec_sha256": normalized_text_sha256(SOURCE_SPEC),
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
            "animation_count": packed_animation_count,
            "asset_count": packed_animation_count + 1,
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
