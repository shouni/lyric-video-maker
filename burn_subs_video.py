#!/usr/bin/env python3
"""Burn one-line styled subtitles onto a finished video (music video).

Where burn_subs.py builds a slideshow from keyframe stills, this keeps the video's picture and camera work
and overlays each line as a transparent PNG with timed ffmpeg overlays (line mode only).

Usage:
    python3 burn_subs_video.py <video.mp4> <subtitles.ass> [output.mp4] [--style-file styles/rock.json]

Subtitles are the ASS from align_subtitles.py (\\k tags are ignored; only line start/end times are used).
"""

import argparse
import os
import subprocess
import sys
import tempfile

from PIL import Image
import pysubs2

from burn_subs import (
    build_render_config,
    load_style_file,
    plain_text,
    render_line_frame,
    require_ffmpeg,
)


def probe_size(video):
    """Get the video resolution with ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def build_windows(subs):
    """Turn ASS events into non-overlapping (start, end, text) display spans.

    Overlapping lines are resolved like burn_subs' event_at: the earlier line wins.
    """
    events = []
    for e in subs:
        plain = plain_text(e.text)
        if plain:
            events.append((e.start / 1000.0, e.end / 1000.0, plain))
    events.sort(key=lambda x: x[0])

    windows = []
    prev_end = 0.0
    for start, end, text in events:
        eff_start = max(start, prev_end)
        if end <= eff_start:
            continue
        windows.append((eff_start, end, text))
        prev_end = end
    return windows


def main():
    parser = argparse.ArgumentParser(description="Burn one-line styled subtitles onto a video.")
    parser.add_argument("video", help="input video (mp4)")
    parser.add_argument("subtitles", help="timed ASS file")
    parser.add_argument("output", nargs="?", default="output_video.mp4", help="output MP4")
    parser.add_argument("--style-file", help="style JSON (presets in styles/)")
    args = parser.parse_args()

    require_ffmpeg("ffmpeg", "ffprobe")
    for path in (args.video, args.subtitles):
        if not os.path.exists(path):
            sys.exit(f"Error: file not found: {path}")

    style_over = load_style_file(args.style_file) if args.style_file else {}
    if style_over.get("mode") == "karaoke":
        print("Warning: burning onto video supports line mode only; ignoring mode=karaoke", file=sys.stderr)

    subs = pysubs2.load(args.subtitles)
    img_w, img_h = probe_size(args.video)
    print(f"Video size: {img_w}x{img_h}")

    cfg = build_render_config(
        subs, (img_w, img_h), style_over, force_mode="line", subs_source=args.subtitles
    )

    windows = build_windows(subs)
    print(f"Subtitle lines: {len(windows)}")

    with tempfile.TemporaryDirectory(prefix="burn_subs_video_") as work_dir:
        inputs = ["-i", args.video]
        filter_parts = []
        prev_label = "0:v"
        for i, (start, end, text) in enumerate(windows):
            layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
            layer = render_line_frame(layer, text, cfg)
            png = os.path.join(work_dir, f"line_{i:03d}.png")
            layer.save(png)
            inputs += ["-i", png]
            out_label = f"v{i + 1}"
            filter_parts.append(
                f"[{prev_label}][{i + 1}:v]overlay=enable='between(t,{start:.3f},{end:.3f})'[{out_label}]"
            )
            prev_label = out_label

        print("\nRunning ffmpeg...")
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error"]
            + inputs
            + ["-filter_complex", ";".join(filter_parts)]
            + ["-map", f"[{prev_label}]", "-map", "0:a?",
               "-c:v", "libx264", "-crf", "18", "-c:a", "copy", args.output],
        )

    if result.returncode == 0:
        size_mb = os.path.getsize(args.output) / 1024 / 1024
        print(f"Done! {args.output} ({size_mb:.1f} MB)")
    else:
        print(f"ffmpeg failed (exit code {result.returncode})")
        sys.exit(1)


if __name__ == "__main__":
    main()
