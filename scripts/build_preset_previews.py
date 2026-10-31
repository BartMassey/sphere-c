#!/usr/bin/env python3
"""Build photographic preset previews from an approved capture index.

Requires FFmpeg on PATH. The capture index and original recordings stay
outside the distributed package; only derived previews and provenance
are written to the destination directory.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import subprocess


def run_ffmpeg(arguments: list[str]) -> bytes:
    """Run FFmpeg and preserve its diagnostic output when a step fails."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *arguments],
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace"))
    return result.stdout


SOURCE_SIZE = (1920, 1080)
DEFAULT_CROP = (752, 288, 448, 448)


def clip_size(clip: Path) -> tuple[int, int]:
    """Read review clip dimensions without invoking a shell."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(clip)],
        capture_output=True, check=True,
    )
    streams = json.loads(result.stdout)["streams"]
    if not streams or streams[0]["width"] <= 0 or streams[0]["height"] <= 0:
        raise ValueError(f"invalid clip dimensions: {clip}")
    return streams[0]["width"], streams[0]["height"]


def scaled_crop(crop: object, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Validate a source-pixel crop and map its edges to another frame size."""
    if (not isinstance(crop, (list, tuple)) or len(crop) != 4
            or any(type(value) is not int for value in crop)):
        raise ValueError("crop_xywh must contain four integers")
    x, y, width, height = crop
    if (x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > SOURCE_SIZE[0] or y + height > SOURCE_SIZE[1]):
        raise ValueError("crop_xywh must fit within the 1920x1080 source")
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError("frame dimensions must be positive")
    left = round(x * size[0] / SOURCE_SIZE[0])
    top = round(y * size[1] / SOURCE_SIZE[1])
    right = round((x + width) * size[0] / SOURCE_SIZE[0])
    bottom = round((y + height) * size[1] / SOURCE_SIZE[1])
    if right <= left or bottom <= top:
        raise ValueError("crop_xywh is too small for the review clip")
    return left, top, right - left, bottom - top


def crop_filter(crop: tuple[int, int, int, int]) -> str:
    """Create an FFmpeg crop filter from validated integer coordinates."""
    x, y, width, height = crop
    return f"crop={width}:{height}:{x}:{y}:exact=1"


def source_path(index_path: Path, name: str) -> Path:
    """Keep capture paths inside the capture index directory."""
    root = index_path.parent.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"capture path outside index directory: {name}")
    return path


def loop_bounds(clip: Path, crop: tuple[int, int, int, int]) -> tuple[float, float]:
    """Find visually similar loop endpoints while retaining at least 8 s.

    Low-resolution samples compare the actual Sphere pixels. Selecting
    endpoints does not synthesize frames or impose a presumed cycle.
    """
    samples = run_ffmpeg([
        "-i", str(clip), "-vf", f"{crop_filter(crop)},fps=5,scale=16:16",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ])
    frame_size = 16 * 16 * 3
    frames = [samples[i:i + frame_size]
              for i in range(0, len(samples), frame_size)]
    if len(frames) < 50:
        raise ValueError(f"clip too short for animation: {clip}")
    best = (float("inf"), 0, 40)
    for start in range(min(16, len(frames) - 40)):
        for end in range(start + 40, min(start + 61, len(frames) - 1)):
            difference = sum(
                (left - right) ** 2
                for left, right in zip(frames[start], frames[end])
            ) / frame_size
            if difference < best[0]:
                best = (difference, start, end)
    return best[1] / 5, (best[2] - best[1]) / 5


def build(index_path: Path, destination: Path) -> None:
    """Build all stills, animation loops, and their package metadata."""
    capture = json.loads(index_path.read_text())
    if capture.get("license") != "CC0-1.0":
        raise ValueError("capture needs explicit CC0-1.0 permission")
    destination.mkdir(parents=True, exist_ok=True)
    correction = capture.get("color_correction")
    color_filter = ""
    if correction is not None:
        if not isinstance(correction, dict) or not isinstance(correction.get("lut"), str):
            raise ValueError("color_correction requires a local LUT filename")
        lut = source_path(index_path, correction["lut"])
        if not lut.is_file():
            raise ValueError("color correction LUT is missing")
        escaped = str(lut).replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
        color_filter = f",lut3d=file='{escaped}':interp=tetrahedral"
    catalog = {}
    provenance = {
        "license": "CC0-1.0", "creator": "Bart Massey",
        "permission": capture["license_permission"],
        "capture_date": capture["date"], "source_recordings": capture["parts"],
        "processing": "Actual camera frames, crop/scale, optional recorded color correction and WebP encoding; no generated artwork.",
        "encoding": {"still": "libwebp, 320x320, quality 90",
                     "animation": "libwebp_anim, 320x320, 15 fps, quality 90"},
        "presets": {},
    }
    if correction is not None:
        provenance["color_correction"] = correction
    for row in capture["presets"]:
        slug = row["slug"]
        if not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", slug):
            raise ValueError(f"invalid preset slug: {slug!r}")
        still = source_path(index_path, row["still"])
        clip = source_path(index_path, row["clip"])
        crop = scaled_crop(row.get("crop_xywh", DEFAULT_CROP), SOURCE_SIZE)
        item = {"category": "animation" if row["classification"] == "Animation" else "pattern",
                "still": slug + ".webp"}
        run_ffmpeg([
            "-i", str(still), "-vf", f"{crop_filter(crop)},scale=320:320:flags=lanczos{color_filter}",
            "-frames:v", "1", "-c:v", "libwebp", "-quality", "90",
            "-y", str(destination / item["still"]),
        ])
        detail = {key: row[key] for key in (
            "label", "value", "classification", "source", "clip_start_seconds",
            "clip_end_seconds", "poster_source_seconds", "independent_readback_matches",
        )}
        detail["crop_xywh_source"] = list(crop)
        if item["category"] == "animation":
            review_crop = scaled_crop(crop, clip_size(clip))
            start, duration = loop_bounds(clip, review_crop)
            item["animation"] = slug + ".animated.webp"
            detail["crop_xywh_review_clip"] = list(review_crop)
            detail["loop_start_seconds_in_take"] = start
            detail["loop_duration_seconds"] = duration
            run_ffmpeg([
                "-ss", str(start), "-i", str(clip), "-t", str(duration),
                "-vf", f"{crop_filter(review_crop)},scale=320:320:flags=lanczos{color_filter},fps=15",
                "-an", "-c:v", "libwebp_anim", "-quality", "90",
                "-loop", "0", "-y", str(destination / item["animation"]),
            ])
        catalog[slug] = item
        provenance["presets"][slug] = detail
        print(f"Built {row['label']} ({item['category']})", flush=True)
    (destination / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (destination / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


def main() -> None:
    """Parse paths for a previously approved recording session."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_index", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build(args.capture_index, args.destination)


if __name__ == "__main__":
    main()
