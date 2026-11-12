#!/usr/bin/env python3
"""Burn styled subtitles onto a finished video (music video).

Where burn_subs.py builds a slideshow from keyframe stills, this keeps the video's picture and camera work.
The subtitles are drawn as transparent PNGs, one per subtitle state, joined into a single transparent track
with ffmpeg concat (the same diff rendering as burn_subs.py) and laid over the video with one overlay.

Usage:
    python3 burn_subs_video.py <video.mp4> <subtitles.ass> [output.mp4] [--style-file styles/rock.json]

Subtitles are the ASS from align_subtitles.py. The default is line mode (\\k tags are ignored; only line
start/end times are used); --karaoke or a style with mode "karaoke" highlights per \\k as burn_subs.py does.
"""

import argparse
import os
import subprocess
import sys
import tempfile

from PIL import Image
import pysubs2

from burn_subs import (
    build_concat_lines,
    build_render_config,
    collect_transition_times,
    load_style_file,
    plain_text,
    require_ffmpeg,
)

BLANK = "blank"  # img_cache key of the transparent layer that stands in for burn_subs' background still


def probe_video(video):
    """Get the video's (width, height, duration in seconds) with ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "default=nw=1", video],
        capture_output=True, text=True, check=True,
    ).stdout
    info = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        duration = float(info.get("duration", ""))
    except ValueError:
        duration = 0.0  # no container duration: the subtitle times alone decide the track length
    return int(info["width"]), int(info["height"]), duration


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


def build_events(subs):
    """ASS events with text as (start s, end s, raw text), sorted by start.

    burn_subs' event_at takes the first event that covers a time, so sorting makes the earlier line win,
    the same rule as build_windows. The raw text keeps its \\k tags (karaoke mode needs them; line mode
    strips them when drawing).
    """
    events = [(e.start / 1000.0, e.end / 1000.0, e.text) for e in subs if plain_text(e.text)]
    events.sort(key=lambda x: x[0])
    return events


def build_overlay_track(events, cfg, video_dur, frames_dir):
    """Draw every subtitle state on a transparent layer and return the ffmpeg-concat lines of the track.

    Reuses burn_subs' build_concat_lines: its background timeline is one transparent layer for the whole
    length, handed over through img_cache so nothing is read from disk.
    """
    last_end = max((end for _, end, _ in events), default=0.0)
    total = max(video_dur, last_end)
    transitions = collect_transition_times(events, total, include_char_transitions=(cfg.mode == "karaoke"))
    img_cache = {BLANK: Image.new("RGBA", cfg.img_size, (0, 0, 0, 0))}
    return build_concat_lines(transitions, [(0.0, total, BLANK)], events, cfg, frames_dir, img_cache=img_cache)


def main():
    parser = argparse.ArgumentParser(description="Burn styled subtitles onto a video.")
    parser.add_argument("video", help="input video (mp4)")
    parser.add_argument("subtitles", help="timed ASS file")
    parser.add_argument("output", nargs="?", default="output_video.mp4", help="output MP4")
    parser.add_argument("--style-file", help="style JSON (presets in styles/)")
    parser.add_argument("--karaoke", action="store_true",
                        help="highlight per \\k like burn_subs.py (default: one plain line at a time)")
    args = parser.parse_args()

    require_ffmpeg("ffmpeg", "ffprobe")
    for path in (args.video, args.subtitles):
        if not os.path.exists(path):
            sys.exit(f"Error: file not found: {path}")

    style_over = load_style_file(args.style_file) if args.style_file else {}
    # Line mode stays the default here (burn_subs.py defaults to karaoke): a style without `mode` gave plain
    # lines on video before karaoke was supported, and should keep doing so.
    mode = "karaoke" if args.karaoke else style_over.get("mode", "line")

    subs = pysubs2.load(args.subtitles)
    img_w, img_h, video_dur = probe_video(args.video)
    print(f"Video size: {img_w}x{img_h}")

    cfg = build_render_config(
        subs, (img_w, img_h), style_over, force_mode=mode, subs_source=args.subtitles
    )

    events = build_events(subs)
    print(f"Subtitle lines: {len(build_windows(subs))}")

    with tempfile.TemporaryDirectory(prefix="burn_subs_video_") as work_dir:
        concat_txt = os.path.join(work_dir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(build_overlay_track(events, cfg, video_dur, work_dir)))

        print("\nRunning ffmpeg...")
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", args.video,
             "-f", "concat", "-safe", "0", "-i", concat_txt,
             # eof_action=pass: once the track ends the video passes through untouched. The default (repeat)
             # would hold the track's last frame, and it also added one frame to the end of the output.
             "-filter_complex", "[0:v][1:v]overlay=eof_action=pass[v]",
             "-map", "[v]", "-map", "0:a?",
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
