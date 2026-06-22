#!/usr/bin/env python3
"""Burn ASS karaoke subtitles onto PNG images and create MP4.

Usage:
    python3 burn_subs.py <audio.mp3> <keyframes.zip> [output.mp4] [--subs <subtitles.ass>]
"""

import re
import os
import sys
import zipfile
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont
import pysubs2

# --- Args ---
if len(sys.argv) < 3:
    print("Usage: python3 burn_subs.py <audio.mp3> <keyframes.zip> [output.mp4] [--subs <subtitles.ass>]")
    sys.exit(1)

AUDIO = sys.argv[1]
ZIP_FILE = sys.argv[2]

# --subs オプションで字幕ファイルを上書き可能
_args = sys.argv[3:]
SUBS_OVERRIDE = None
OUTPUT = "output.mp4"
i = 0
while i < len(_args):
    if _args[i] == "--subs" and i + 1 < len(_args):
        SUBS_OVERRIDE = _args[i + 1]
        i += 2
    else:
        OUTPUT = _args[i]
        i += 1
FRAMES_DIR = "frames_tmp"

# --- Extract zip ---
work_dir = tempfile.mkdtemp(prefix="burn_subs_")
print(f"Extracting {ZIP_FILE} -> {work_dir}")
with zipfile.ZipFile(ZIP_FILE) as z:
    z.extractall(work_dir)

# --- Read inputs.txt ---
inputs_txt = os.path.join(work_dir, "inputs.txt")
images_with_durations = []
current_file = None
with open(inputs_txt) as f:
    for line in f:
        line = line.strip()
        if line.startswith("file "):
            current_file = line.split("'")[1]
        elif line.startswith("duration "):
            dur = float(line.split()[1])
            images_with_durations.append((current_file, dur))

print(f"Images: {[f for f,_ in images_with_durations]}")

# --- Detect image size from first image ---
first_img_path = os.path.join(work_dir, images_with_durations[0][0])
with Image.open(first_img_path) as probe:
    IMG_W, IMG_H = probe.size
print(f"Image size: {IMG_W}x{IMG_H}")

# --- ASS style parameters ---
subtitle_file = SUBS_OVERRIDE if SUBS_OVERRIDE else os.path.join(work_dir, "subtitles.ass")
subs_raw = pysubs2.load(subtitle_file)

# Read PlayResY from script info
PLAY_RES_Y = subs_raw.info.get("PlayResY", "1080")
PLAY_RES_Y = int(PLAY_RES_Y)
SCALE = IMG_H / PLAY_RES_Y

# Read style
style = subs_raw.styles.get("Karaoke") or list(subs_raw.styles.values())[0]
FONT_SIZE = int(style.fontsize * SCALE)
MARGIN_V = int(style.marginv * SCALE)
OUTLINE = max(1, int(style.outline * SCALE))

def color_to_rgb(c):
    return (c.r, c.g, c.b)

PRIMARY = color_to_rgb(style.primarycolor)
SECONDARY = color_to_rgb(style.secondarycolor)
OUTLINE_COLOR = color_to_rgb(style.outlinecolor)

print(f"Font size: {FONT_SIZE}, MarginV: {MARGIN_V}, Outline: {OUTLINE}")
print(f"Primary: {PRIMARY}, Secondary: {SECONDARY}")

# --- Font ---
FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]
font = None
for path in FONT_CANDIDATES:
    if os.path.exists(path):
        try:
            font = ImageFont.truetype(path, FONT_SIZE)
            print(f"Font: {path}")
            break
        except Exception:
            continue
if font is None:
    print("Warning: no CJK font found, using default")
    font = ImageFont.load_default()

# --- Karaoke parser ---
def parse_karaoke(raw_text):
    segments = []
    cs = 0
    for m in re.finditer(r'\{\\k(\d+)\}([^{]*)', raw_text):
        k = int(m.group(1))
        text = m.group(2)
        segments.append((cs, cs + k, text))
        cs += k
    return segments

# --- Build image timeline ---
timeline = []
t = 0.0
for img, dur in images_with_durations:
    timeline.append((t, t + dur, os.path.join(work_dir, img)))
    t += dur
total_dur = t

def img_at(t_sec):
    for start, end, img in timeline:
        if start <= t_sec < end:
            return img
    return timeline[-1][2]

# --- Load subtitle events ---
events = [(e.start / 1000.0, e.end / 1000.0, e.text) for e in subs_raw]

def event_at(t_sec):
    for start, end, text in events:
        if start <= t_sec < end:
            return (start, end, text)
    return None

# --- Collect all karaoke transition times ---
transitions = {0.0, total_dur}
for start, end, text in events:
    transitions.add(start)
    transitions.add(end)
    for seg_cs_s, seg_cs_e, _ in parse_karaoke(text):
        transitions.add(start + seg_cs_s / 100.0)
        transitions.add(start + seg_cs_e / 100.0)

transitions = sorted(transitions)
print(f"Total transition segments: {len(transitions) - 1}")

# --- Rendering ---
os.makedirs(FRAMES_DIR, exist_ok=True)
img_cache = {}

def get_base_img(path):
    if path not in img_cache:
        img_cache[path] = Image.open(path).convert("RGB")
    return img_cache[path].copy()

def render_frame(img_path, evt, elapsed_cs):
    img = get_base_img(img_path)
    if evt is None:
        return img

    segs = parse_karaoke(evt[2])
    if not segs:
        return img

    n_highlighted = sum(1 for (s, e, _) in segs if elapsed_cs >= s)

    draw = ImageDraw.Draw(img)
    full_text = "".join(t for _, _, t in segs)

    bbox = draw.textbbox((0, 0), full_text, font=font)
    total_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x = (IMG_W - total_w) // 2
    y = IMG_H - text_h - MARGIN_V

    cur_x = x
    for i, (s, e, seg_text) in enumerate(segs):
        color = PRIMARY if i < n_highlighted else SECONDARY
        draw.text(
            (cur_x, y), seg_text, font=font,
            fill=color,
            stroke_width=OUTLINE,
            stroke_fill=OUTLINE_COLOR,
        )
        seg_bbox = draw.textbbox((0, 0), seg_text, font=font)
        cur_x += seg_bbox[2] - seg_bbox[0]

    return img

# --- Render unique frames and build concat list ---
concat_lines = []
rendered_cache = {}
last_frame_path = None

for i in range(len(transitions) - 1):
    seg_start = transitions[i]
    seg_end = transitions[i + 1]
    dur = seg_end - seg_start
    if dur < 1e-6:
        continue

    mid = (seg_start + seg_end) / 2
    img_file = img_at(mid)
    evt = event_at(mid)

    elapsed_cs = (mid - evt[0]) * 100 if evt else 0
    n_hl = 0
    if evt:
        segs = parse_karaoke(evt[2])
        n_hl = sum(1 for (s, e, _) in segs if elapsed_cs >= s)

    state_key = (img_file, evt[0] if evt else None, n_hl)

    if state_key not in rendered_cache:
        base = os.path.splitext(os.path.basename(img_file))[0]
        frame_path = os.path.join(
            FRAMES_DIR,
            f"frame_{base}_{str(evt[0] if evt else 'x').replace('.','_')}_{n_hl}.png"
        )
        img = render_frame(img_file, evt, elapsed_cs)
        img.save(frame_path)
        rendered_cache[state_key] = frame_path
        print(f"  Rendered: {os.path.basename(frame_path)}")

    frame_path = rendered_cache[state_key]
    last_frame_path = frame_path
    concat_lines.append(f"file '{os.path.abspath(frame_path)}'")
    concat_lines.append(f"duration {dur:.6f}")

if last_frame_path:
    concat_lines.append(f"file '{os.path.abspath(last_frame_path)}'")

concat_txt = os.path.join(FRAMES_DIR, "concat.txt")
with open(concat_txt, "w") as f:
    f.write("\n".join(concat_lines))

print(f"\nRunning ffmpeg...")
result = subprocess.run(
    [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_txt,
        "-i", AUDIO,
        "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        OUTPUT,
    ],
    capture_output=True, text=True,
)

if result.returncode == 0:
    size_mb = os.path.getsize(OUTPUT) / 1024 / 1024
    print(f"Done! {OUTPUT} ({size_mb:.1f} MB)")
else:
    print(f"ffmpeg error:\n{result.stderr[-3000:]}")
