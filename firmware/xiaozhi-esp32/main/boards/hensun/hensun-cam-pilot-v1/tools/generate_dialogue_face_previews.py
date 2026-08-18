#!/usr/bin/env python3
"""Generate review-only Hensun dialogue faces with natural five-pose mouths."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import generate_emote_lab_assets as base
from PIL import Image, ImageDraw, ImageFont

BOARD = Path(__file__).resolve().parents[1]
ASSET_ROOT = BOARD / "emote_lab"
SOURCE_SPEC = ASSET_ROOT / "source/hensun_dialogue_face_spec.json"
PREVIEW_ROOT = ASSET_ROOT / "dialogue_preview"
GIF_ROOT = PREVIEW_ROOT / "gifs"
RUNTIME_ROOT = ASSET_ROOT / "dialogue_runtime"
RUNTIME_GIF_ROOT = RUNTIME_ROOT / "gifs"
CONTACT_SHEET = PREVIEW_ROOT / "hensun_dialogue_faces_contact_sheet.png"
MOTION_PREVIEW = PREVIEW_ROOT / "hensun_dialogue_faces_motion_preview.gif"
SPEAKING_PREVIEW = PREVIEW_ROOT / "hensun_speaking_runtime_preview.gif"
MOUTH_SHEET = PREVIEW_ROOT / "hensun_natural_mouth_5pose.png"
MANIFEST = PREVIEW_ROOT / "manifest.json"
CONTACT_COLUMNS = 5
INITIAL_RUNTIME_EXPRESSIONS = ("neutral", "happy", "caring")


def _eyes_neutral(image: Image.Image, active: float, phase: float) -> None:
    breathe = math.sin(phase * math.tau) * 1.0
    blink = base.clamp(1.0 - abs(phase - 0.72) / 0.05)
    for side, x in ((-1, 78), (1, 162)):
        height = base.lerp(10, 76 + breathe, active) * (1.0 - blink * 0.86)
        base.eye_blob(
            image,
            (x, 142 + breathe),
            (62, max(9, height)),
            (-side * 3, 7),
            (17, max(10, min(25, height * 0.34))),
            angle=side,
            lid=0.04,
            alpha=min(1.0, active + 0.16),
        )


def _eyes_happy(image: Image.Image, active: float, phase: float) -> None:
    bounce = math.sin(phase * math.tau) * 1.4
    for side, x in ((-1, 78), (1, 162)):
        base.arc_stroke(
            image,
            (x, 145 + bounce),
            (64, 42),
            198,
            342,
            11,
            angle=side * 1.5,
            alpha=active,
        )
    base.cheek_dashes(image, 187 + bounce, active * 0.88, 1)


def _eyes_laughing(image: Image.Image, active: float, phase: float) -> None:
    beat = 0.5 - 0.5 * math.cos(phase * math.tau * 2)
    for side, x in ((-1, 78), (1, 162)):
        base.arc_stroke(
            image,
            (x, 137 - beat * 3),
            (72 + beat * 3, 54 - beat * 4),
            198,
            342,
            12,
            angle=side * 2,
            alpha=active,
        )
    base.cheek_dashes(image, 184 + beat * 2, active, 4)


def _eyes_caring(image: Image.Image, active: float, phase: float) -> None:
    breathe = math.sin(phase * math.tau) * 0.9
    for side, x in ((-1, 78), (1, 162)):
        base.eye_blob(
            image,
            (x, 143 + breathe),
            (64, base.lerp(10, 74 + breathe, active)),
            (-side * 5, 10),
            (18, 24),
            angle=side * -1.5,
            lid=0.09,
            alpha=min(1.0, active + 0.16),
        )
        base.arc_stroke(
            image,
            (x, 94 + breathe),
            (42, 17),
            205,
            335,
            5,
            angle=side * -7,
            alpha=active * 0.72,
        )


def _eyes_affectionate(image: Image.Image, active: float, phase: float) -> None:
    settle = math.sin(phase * math.tau) * 1.8
    for side, x in ((-1, 77), (1, 163)):
        base.eye_blob(
            image,
            (x - side * settle, 140 + settle * 0.4),
            (72, base.lerp(10, 91, active)),
            (-side * 9, 7),
            (23, 31),
            angle=side * -1.5,
            lid=0.10,
            alpha=min(1.0, active + 0.18),
        )
    base.cheek_dashes(image, 187, active, 3)
    sparkle = 0.45 + 0.45 * (0.5 - 0.5 * math.cos(phase * math.tau))
    base.ellipse(image, (205, 92 - settle), (6, 6), alpha=active * sparkle)


def _eyes_curious(image: Image.Image, active: float, phase: float) -> None:
    glance = math.sin(phase * math.tau) * 5
    base.eye_blob(
        image,
        (77, 139),
        (69, base.lerp(10, 86, active)),
        (9 + glance, -7),
        (19, 28),
        angle=-4,
        lid=0.10,
        alpha=min(1.0, active + 0.18),
    )
    base.eye_blob(
        image,
        (163, 143),
        (65, base.lerp(10, 73, active)),
        (6 + glance * 0.4, 6),
        (18, 24),
        angle=4,
        lid=0.18,
        alpha=min(1.0, active + 0.18),
    )
    base.arc_stroke(image, (74, 87), (49, 22), 205, 335, 7, angle=10, alpha=active)


def _eyes_surprised(image: Image.Image, active: float, phase: float) -> None:
    pulse = 0.5 - 0.5 * math.cos(phase * math.tau)
    for side, x in ((-1, 77), (1, 163)):
        base.eye_blob(
            image,
            (x, 139 - pulse * 2),
            (68 + pulse * 2, base.lerp(10, 96 + pulse * 3, active)),
            (-side, 3),
            (18, 31),
            angle=side,
            lid=0.0,
            alpha=min(1.0, active + 0.18),
        )
        base.arc_stroke(
            image, (x, 84 - pulse * 2), (49, 22), 205, 335, 7, angle=side * 5, alpha=active
        )


def _eyes_confused(image: Image.Image, active: float, phase: float) -> None:
    wobble = math.sin(phase * math.tau) * 3.5
    base.eye_blob(
        image,
        (77, 142 + wobble * 0.35),
        (68, base.lerp(10, 78, active)),
        (7, 6),
        (18, 25),
        angle=-7,
        lid=0.24,
        alpha=min(1.0, active + 0.16),
    )
    base.eye_blob(
        image,
        (163, 139 - wobble * 0.35),
        (67, base.lerp(10, 77, active)),
        (-8, -3),
        (18, 25),
        angle=7,
        lid=0.08,
        alpha=min(1.0, active + 0.16),
    )
    base.arc_stroke(image, (74, 87), (47, 21), 205, 335, 7, angle=13, alpha=active)
    base.arc_stroke(image, (166, 93), (42, 18), 205, 335, 6, angle=-14, alpha=active)


def _eyes_concerned(image: Image.Image, active: float, phase: float) -> None:
    settle = math.sin(phase * math.tau) * 1.2
    for side, x in ((-1, 78), (1, 162)):
        base.eye_blob(
            image,
            (x, 143 + settle),
            (68, base.lerp(10, 82, active)),
            (-side * 7, 11),
            (19, 26),
            angle=side * -2,
            lid=0.18,
            alpha=min(1.0, active + 0.16),
        )
        base.arc_stroke(image, (x, 89), (47, 22), 205, 335, 6, angle=side * -11, alpha=active)


def _eyes_apologetic(image: Image.Image, active: float, phase: float) -> None:
    breathe = math.sin(phase * math.tau) * 1.2
    for side, x in ((-1, 78), (1, 162)):
        base.eye_blob(
            image,
            (x, 146 + breathe),
            (64, base.lerp(10, 68, active)),
            (-side * 6, 13),
            (17, 21),
            angle=side * -4,
            lid=0.34,
            alpha=min(1.0, active + 0.16),
        )
        base.arc_stroke(
            image, (x, 99 + breathe), (43, 19), 205, 335, 6, angle=side * 8, alpha=active * 0.92
        )


EYE_DRAWERS = {
    "neutral": _eyes_neutral,
    "happy": _eyes_happy,
    "laughing": _eyes_laughing,
    "caring": _eyes_caring,
    "affectionate": _eyes_affectionate,
    "curious": _eyes_curious,
    "surprised": _eyes_surprised,
    "confused": _eyes_confused,
    "concerned": _eyes_concerned,
    "apologetic": _eyes_apologetic,
}


def _draw_mouth_curve(
    image: Image.Image, half_width: int, depth: int, thickness: int, active: float
) -> None:
    center_x = 160
    center_y = 170
    divisor = max(1, half_width * half_width)
    points = []
    for x in range(-half_width, half_width + 1):
        y = center_y + depth - (depth * x * x) / divisor
        points.append((int(round((center_x + x) * base.SCALE)), int(round(y * base.SCALE))))
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.line(
        points,
        fill=(247, 247, 242, int(round(255 * base.clamp(active)))),
        width=max(1, thickness * base.SCALE),
        joint="curve",
    )
    image.alpha_composite(layer)


def draw_natural_mouth(image: Image.Image, pose: int, active: float, emotion: str) -> None:
    pose = max(0, min(4, pose))
    del emotion
    poses = ((10, 2, 2), (12, 3, 2), (14, 5, 3), (16, 7, 3), (18, 9, 4))
    _draw_mouth_curve(image, *poses[pose], active)


def render_frame(
    name: str,
    frame_index: int,
    animation: dict[str, object],
    *,
    include_mouth: bool = True,
    runtime: bool = False,
) -> Image.Image:
    total = int(animation["frames"])
    loop_start = int(animation["loop_start_frame"])
    loop_end = int(animation["loop_end_frame"])
    if runtime:
        active = 1.0
        phase = frame_index / max(1, total)
    else:
        active, phase = base.stage(frame_index, total, loop_start, loop_end)
    canvas = Image.new(
        "RGBA", (base.CANVAS_WIDTH * base.SCALE, base.CANVAS_HEIGHT * base.SCALE), (0, 0, 0, 255)
    )
    if not runtime:
        base.draw_bridge(canvas, base.clamp(1.0 - active))
    EYE_DRAWERS[name](canvas, active, phase)
    if include_mouth:
        mouth_sequence = (0, 1, 2, 3, 2, 1, 2, 3, 4, 3, 2, 1)
        pose = mouth_sequence[int(phase * len(mouth_sequence)) % len(mouth_sequence)]
        draw_natural_mouth(canvas, pose, active, name)
    output = canvas.convert("RGB").resize(
        (base.CANVAS_WIDTH, base.CANVAS_HEIGHT), Image.Resampling.LANCZOS
    )
    output.putpixel(
        (base.CANVAS_WIDTH - 32 + frame_index % 31, base.CANVAS_HEIGHT - 1), (247, 247, 242)
    )
    return output


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 17)
    except OSError:
        return ImageFont.load_default()


def _labeled_tile(frame: Image.Image, name: str) -> Image.Image:
    tile = Image.new("RGB", (base.CANVAS_WIDTH, base.CANVAS_HEIGHT + 28), (18, 18, 18))
    tile.paste(frame, (0, 0))
    draw = ImageDraw.Draw(tile)
    draw.text((12, base.CANVAS_HEIGHT + 5), name, fill=(247, 247, 242), font=_font())
    return tile


def build(check: bool) -> int:
    spec = json.loads(SOURCE_SPEC.read_text(encoding="utf-8"))
    if spec["canvas"] != {"width": 320, "height": 240, "fps": 20}:
        raise SystemExit("dialogue face canvas must be 320x240 at 20 FPS")
    if tuple(spec["expressions"]) != tuple(EYE_DRAWERS):
        raise SystemExit("dialogue face spec and renderer order differ")

    GIF_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME_GIF_ROOT.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, list[Image.Image]] = {}
    manifest_expressions: dict[str, dict[str, object]] = {}
    for name, animation in spec["expressions"].items():
        frames = [render_frame(name, index, animation) for index in range(animation["frames"])]
        rendered[name] = frames
        output = GIF_ROOT / animation["export_file"]
        if check:
            temporary = output.with_suffix(".check.gif")
            base.save_gif(temporary, frames)
            try:
                if not output.is_file() or temporary.read_bytes() != output.read_bytes():
                    raise SystemExit(f"dialogue preview is stale: {output}")
            finally:
                temporary.unlink(missing_ok=True)
        else:
            base.save_gif(output, frames)
        manifest_expressions[name] = {
            "file": str(output.relative_to(PREVIEW_ROOT)).replace("\\", "/"),
            "frames": animation["frames"],
            "loop_start_frame": animation["loop_start_frame"],
            "loop_end_frame": animation["loop_end_frame"],
            "sha256": _sha256(output),
        }

    runtime_expressions: dict[str, dict[str, object]] = {}
    for name in INITIAL_RUNTIME_EXPRESSIONS:
        animation = spec["expressions"][name]
        runtime_frames = [
            render_frame(name, index, animation, include_mouth=False, runtime=True)
            for index in range(animation["frames"])
        ]
        output = RUNTIME_GIF_ROOT / f"talk_{name}.gif"
        if check:
            temporary = output.with_suffix(".check.gif")
            base.save_gif(temporary, runtime_frames)
            try:
                if not output.is_file() or temporary.read_bytes() != output.read_bytes():
                    raise SystemExit(f"dialogue runtime asset is stale: {output}")
            finally:
                temporary.unlink(missing_ok=True)
        else:
            base.save_gif(output, runtime_frames)
        runtime_expressions[name] = {
            "file": str(output.relative_to(RUNTIME_ROOT)).replace("\\", "/"),
            "frames": animation["frames"],
            "loop_start_frame": 0,
            "loop_end_frame": animation["frames"],
            "sha256": _sha256(output),
        }

    rows = math.ceil(len(rendered) / CONTACT_COLUMNS)
    sheet = Image.new(
        "RGB", (base.CANVAS_WIDTH * CONTACT_COLUMNS, (base.CANVAS_HEIGHT + 28) * rows), (0, 0, 0)
    )
    for index, (name, animation) in enumerate(spec["expressions"].items()):
        frame = rendered[name][animation["loop_start_frame"] + 8]
        sheet.paste(
            _labeled_tile(frame, name),
            (
                (index % CONTACT_COLUMNS) * base.CANVAS_WIDTH,
                (index // CONTACT_COLUMNS) * (base.CANVAS_HEIGHT + 28),
            ),
        )

    motion_frames: list[Image.Image] = []
    for frame_index in range(32):
        motion = Image.new("RGB", sheet.size, (0, 0, 0))
        for index, name in enumerate(spec["expressions"]):
            frame = rendered[name][frame_index % len(rendered[name])]
            motion.paste(
                _labeled_tile(frame, name),
                (
                    (index % CONTACT_COLUMNS) * base.CANVAS_WIDTH,
                    (index // CONTACT_COLUMNS) * (base.CANVAS_HEIGHT + 28),
                ),
            )
        motion_frames.append(motion)

    speaking_preview_frames: list[Image.Image] = []
    speaking_names = ("neutral", "happy", "caring")
    for frame_index in range(48):
        preview = Image.new(
            "RGB",
            (base.CANVAS_WIDTH * len(speaking_names), base.CANVAS_HEIGHT + 28),
            (0, 0, 0),
        )
        for index, name in enumerate(speaking_names):
            animation = spec["expressions"][name]
            frame = render_frame(
                name,
                frame_index % int(animation["frames"]),
                animation,
                runtime=True,
            )
            preview.paste(_labeled_tile(frame, name), (index * base.CANVAS_WIDTH, 0))
        speaking_preview_frames.append(preview)

    mouth_sheet = Image.new("RGB", (base.CANVAS_WIDTH * 5, base.CANVAS_HEIGHT + 28), (0, 0, 0))
    for pose, name in enumerate(("closed", "light", "narrow", "round", "wide")):
        canvas = Image.new(
            "RGBA",
            (base.CANVAS_WIDTH * base.SCALE, base.CANVAS_HEIGHT * base.SCALE),
            (0, 0, 0, 255),
        )
        _eyes_neutral(canvas, 1.0, 0.25)
        draw_natural_mouth(canvas, pose, 1.0, "neutral")
        frame = canvas.convert("RGB").resize(
            (base.CANVAS_WIDTH, base.CANVAS_HEIGHT), Image.Resampling.LANCZOS
        )
        mouth_sheet.paste(_labeled_tile(frame, name), (pose * base.CANVAS_WIDTH, 0))

    if check:
        temporary_sheet = CONTACT_SHEET.with_suffix(".check.png")
        sheet.save(temporary_sheet, optimize=True)
        temporary_mouth_sheet = MOUTH_SHEET.with_suffix(".check.png")
        mouth_sheet.save(temporary_mouth_sheet, optimize=True)
        temporary_motion = MOTION_PREVIEW.with_suffix(".check.gif")
        base.save_gif(temporary_motion, motion_frames)
        temporary_speaking = SPEAKING_PREVIEW.with_suffix(".check.gif")
        base.save_gif(temporary_speaking, speaking_preview_frames)
        try:
            if (
                not CONTACT_SHEET.is_file()
                or temporary_sheet.read_bytes() != CONTACT_SHEET.read_bytes()
            ):
                raise SystemExit(f"dialogue contact sheet is stale: {CONTACT_SHEET}")
            if (
                not MOTION_PREVIEW.is_file()
                or temporary_motion.read_bytes() != MOTION_PREVIEW.read_bytes()
            ):
                raise SystemExit(f"dialogue motion preview is stale: {MOTION_PREVIEW}")
            if (
                not SPEAKING_PREVIEW.is_file()
                or temporary_speaking.read_bytes() != SPEAKING_PREVIEW.read_bytes()
            ):
                raise SystemExit(f"speaking runtime preview is stale: {SPEAKING_PREVIEW}")
            if (
                not MOUTH_SHEET.is_file()
                or temporary_mouth_sheet.read_bytes() != MOUTH_SHEET.read_bytes()
            ):
                raise SystemExit(f"natural mouth sheet is stale: {MOUTH_SHEET}")
        finally:
            temporary_sheet.unlink(missing_ok=True)
            temporary_mouth_sheet.unlink(missing_ok=True)
            temporary_motion.unlink(missing_ok=True)
            temporary_speaking.unlink(missing_ok=True)
    else:
        sheet.save(CONTACT_SHEET, optimize=True)
        mouth_sheet.save(MOUTH_SHEET, optimize=True)
        base.save_gif(MOTION_PREVIEW, motion_frames)
        base.save_gif(SPEAKING_PREVIEW, speaking_preview_frames)

    manifest = {
        "asset_set": spec["asset_set"],
        "source_spec": str(SOURCE_SPEC.relative_to(ASSET_ROOT)).replace("\\", "/"),
        "source_spec_sha256": base.normalized_text_sha256(SOURCE_SPEC),
        "canvas": spec["canvas"],
        "palette": spec["palette"],
        "mouth": spec["mouth"],
        "expressions": manifest_expressions,
        "contact_sheet": {"file": CONTACT_SHEET.name, "sha256": _sha256(CONTACT_SHEET)},
        "motion_preview": {"file": MOTION_PREVIEW.name, "sha256": _sha256(MOTION_PREVIEW)},
        "speaking_preview": {
            "file": SPEAKING_PREVIEW.name,
            "sha256": _sha256(SPEAKING_PREVIEW),
        },
        "mouth_sheet": {"file": MOUTH_SHEET.name, "sha256": _sha256(MOUTH_SHEET)},
    }
    content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    runtime_manifest = {
        "asset_set": "hensun-dialogue-runtime-v1",
        "source_spec": str(SOURCE_SPEC.relative_to(ASSET_ROOT)).replace("\\", "/"),
        "source_spec_sha256": base.normalized_text_sha256(SOURCE_SPEC),
        "canvas": spec["canvas"],
        "palette": spec["palette"],
        "mouth_layer": "firmware PCM overlay",
        "expressions": runtime_expressions,
    }
    runtime_content = json.dumps(runtime_manifest, ensure_ascii=False, indent=2) + "\n"
    if check:
        if not MANIFEST.is_file() or MANIFEST.read_text(encoding="utf-8") != content:
            raise SystemExit(f"dialogue preview manifest is stale: {MANIFEST}")
        runtime_manifest_path = RUNTIME_ROOT / "manifest.json"
        if (
            not runtime_manifest_path.is_file()
            or runtime_manifest_path.read_text(encoding="utf-8") != runtime_content
        ):
            raise SystemExit(f"dialogue runtime manifest is stale: {runtime_manifest_path}")
    else:
        MANIFEST.write_text(content, encoding="utf-8", newline="\n")
        (RUNTIME_ROOT / "manifest.json").write_text(
            runtime_content, encoding="utf-8", newline="\n"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify committed preview outputs")
    return build(parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
