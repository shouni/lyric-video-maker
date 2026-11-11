#!/usr/bin/env python3
"""Burn ASS karaoke subtitles onto keyframe images and produce an MP4.

Usage:
    python3 burn_subs.py <audio.mp3> <keyframes.zip> [output.mp4] [--subs <subtitles.ass>] [--style-file <style.json>]

--style-file overrides the drawing style with JSON (presets in styles/).
mode="line" shows one line without karaoke highlighting.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFont
import pysubs2


@dataclass
class RenderConfig:
    font: Any
    img_size: tuple
    margin_v: int
    outline: int
    primary: tuple
    secondary: tuple
    outline_color: tuple
    mode: str = "karaoke"  # "karaoke" | "line"
    position: str = "bottom"  # "top" | "center" | "bottom" (line mode only)
    letter_spacing: int = 0  # letter spacing in px (line mode only)
    box: Any = None  # background box as ((r, g, b, a), pad_px); None disables it


HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
TAG_RE = re.compile(r"\{[^}]*\}")
LINE_BREAK_RE = re.compile(r"\\[nNh]")


def hex_to_rgb(value):
    """Convert '#RRGGBB' to an RGB tuple."""
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def require_ffmpeg(*tools):
    """Check that the required external commands exist; stop with install instructions if not.

    Without ffmpeg, subprocess's FileNotFoundError is hard to read, so this is checked before any work starts.
    """
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise SystemExit(f"Error: {', '.join(missing)} not found (macOS: brew install ffmpeg)")


FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
    os.path.expanduser("~/Library/Fonts/SourceHanSans-VF.otf.ttc"),
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def safe_extract(zip_file, target_dir):
    """Extract a ZIP. Stops instead of extracting a path that would land outside target_dir."""
    abs_target = os.path.abspath(target_dir) + os.sep
    for member in zip_file.infolist():
        dest = os.path.abspath(os.path.join(abs_target, member.filename))
        if not dest.startswith(abs_target):
            raise ValueError(f"Attempted path traversal in zip: {member.filename}")
    zip_file.extractall(target_dir)


def color_to_rgb(c):
    """Convert a pysubs2 ASS colour to an RGB tuple for PIL."""
    return (c.r, c.g, c.b)


def parse_karaoke(raw_text):
    """Split ASS \\k tags into a list of (start cs, end cs, text) segments."""
    segments = []
    cs = 0
    for m in re.finditer(r'\{\\k(\d+)\}([^{]*)', raw_text):
        k = int(m.group(1))
        text = LINE_BREAK_RE.sub("", m.group(2))
        segments.append((cs, cs + k, text))
        cs += k
    return segments


def plain_text(raw_text):
    """Remove override tags (\\k etc.) and line-break tags from an ASS event's text."""
    return LINE_BREAK_RE.sub("", TAG_RE.sub("", raw_text)).strip()


def highlighted_count(segs, elapsed_cs):
    """Number of highlighted \\k segments elapsed_cs after the line starts."""
    return sum(1 for start_cs, _end_cs, _text in segs if elapsed_cs >= start_cs)


def read_images_with_durations(inputs_txt):
    """Read an ffmpeg-concat inputs.txt as a list of (image file name, seconds shown)."""
    images_with_durations = []
    current_file = None
    with open(inputs_txt, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("file "):
                current_file = line.split("'")[1]
            elif line.startswith("duration "):
                dur = float(line.split()[1])
                images_with_durations.append((current_file, dur))
    return images_with_durations


def build_timeline(work_dir, images_with_durations):
    """From the display lengths, compute each image's absolute time span and the total length."""
    timeline = []
    t = 0.0
    for img, dur in images_with_durations:
        timeline.append((t, t + dur, os.path.join(work_dir, img)))
        t += dur
    return timeline, t


def img_at(timeline, t_sec):
    """Path of the image shown at the given time (seconds)."""
    for start, end, img in timeline:
        if start <= t_sec < end:
            return img
    return timeline[-1][2]


def event_at(events, t_sec):
    """Subtitle event shown at the given time (seconds). When lines overlap, the earlier one wins."""
    for start, end, text in events:
        if start <= t_sec < end:
            return (start, end, text)
    return None


def load_font(font_size):
    """Find and load a font that can draw Japanese, in FONT_CANDIDATES order."""
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, font_size)
                print(f"Font: {path}")
                return font
            except Exception:
                continue

    print("Warning: no CJK font found, using default")
    return ImageFont.load_default()


def get_base_img(path, img_cache):
    """Return a copy of the background image from the cache (so the same image is not read again)."""
    if path not in img_cache:
        img_cache[path] = Image.open(path).convert("RGB")
    return img_cache[path].copy()


LINE_MAX_WIDTH_RATIO = 0.92  # widest share of the image a line may take
LINE_SHRINK_FLOOR = 0.7  # smallest shrink allowed on one line; below this, wrap into two lines
MIN_FONT_SIZE = 12  # lower bound for auto-shrinking (smaller is unreadable)


def measure_line(draw, text, font, spacing):
    """Rendered width of one line, letter spacing included."""
    if spacing > 0:
        return sum(draw.textlength(ch, font=font) for ch in text) + spacing * max(0, len(text) - 1)
    return draw.textlength(text, font=font)


def best_split_index(draw, units, font, spacing):
    """Return where to split a list of text units (single characters or \\k segments) into two lines.

    Picks the split that minimises the measured width of the longer line. If some units end in a space,
    only the points right after them are candidates (English words are not split); otherwise every boundary
    is (Japanese lyrics). Units themselves are never split. Returns None when every split leaves one side empty.
    """
    candidates = [i + 1 for i, u in enumerate(units[:-1]) if u.endswith(" ")]
    if not candidates:
        candidates = list(range(1, len(units)))
    best_idx, best_w = None, None
    for idx in candidates:
        l1 = "".join(units[:idx]).strip()
        l2 = "".join(units[idx:]).strip()
        if not l1 or not l2:
            continue
        w = max(measure_line(draw, l1, font, spacing), measure_line(draw, l2, font, spacing))
        if best_w is None or w < best_w:
            best_idx, best_w = idx, w
    return best_idx


def best_split(draw, text, font, spacing):
    """Split one line of text into two (by character). Returns (text, "") if it cannot be split."""
    idx = best_split_index(draw, list(text), font, spacing)
    if idx is None:
        return text, ""
    return text[:idx].strip(), text[idx:].strip()


def shrink_to_fit(draw, lines, font, spacing, max_w):
    """Shrink the font until the given lines fit max_w.

    If they still do not fit at MIN_FONT_SIZE, it stops there (overflowing beats unreadable subtitles).
    The returned font and size always match.
    """
    size = getattr(font, "size", 0) or 0
    if size <= MIN_FONT_SIZE:
        return font, size
    while True:
        f = font.font_variant(size=size)
        if size <= MIN_FONT_SIZE or all(measure_line(draw, ln, f, spacing) <= max_w for ln in lines):
            return f, size
        size -= 2


def fit_units(draw, units, cfg, spacing, split_texts):
    """Choose a font that fits the units within the image width, and where to split into two lines (None for one line).

    It first tries shrinking on one line; if that would go below LINE_SHRINK_FLOOR, it wraps into two lines
    and shrinks those to fit. split_texts(idx) returns the two strings actually drawn when split at idx
    (used to judge the shrink).
    """
    max_w = cfg.img_size[0] * LINE_MAX_WIDTH_RATIO
    font = cfg.font
    base_size = getattr(font, "size", 0) or 0
    full_text = "".join(units)
    if base_size <= 0 or measure_line(draw, full_text, font, spacing) <= max_w:
        return font, None

    _, size_one_line = shrink_to_fit(draw, [full_text], font, spacing, max_w)
    if size_one_line >= base_size * LINE_SHRINK_FLOOR:
        return font.font_variant(size=size_one_line), None

    idx = best_split_index(draw, units, font, spacing)
    if idx is None:
        return font.font_variant(size=size_one_line), None
    fitted, _ = shrink_to_fit(draw, split_texts(idx), font, spacing, max_w)
    return fitted, idx


def fit_line_layout(draw, text, cfg):
    """Choose a font and a list of lines (1–2, split by character) so one line of text fits the image width."""
    def split_texts(idx):
        return [text[:idx].strip(), text[idx:].strip()]

    font, idx = fit_units(draw, list(text), cfg, cfg.letter_spacing, split_texts)
    return font, [text] if idx is None else split_texts(idx)


def best_split_segs(draw, segs, font, spacing):
    """Split karaoke \\k segments into two lines (by segment). Returns (segs, []) if it cannot be split.

    In existing ASS files, punctuation and trailing spaces are folded into the segment before, so segment
    boundaries match best_split's character / space boundaries.
    """
    idx = best_split_index(draw, [t for _, _, t in segs], font, spacing)
    if idx is None:
        return segs, []
    return segs[:idx], segs[idx:]


def fit_karaoke_layout(draw, segs, cfg):
    """Choose a font and a line split (1–2 lines) so the karaoke \\k segments fit the image width."""
    texts = [t for _, _, t in segs]

    def split_texts(idx):
        return ["".join(texts[:idx]), "".join(texts[idx:])]

    font, idx = fit_units(draw, texts, cfg, 0, split_texts)
    return font, [segs] if idx is None else [segs[:idx], segs[idx:]]


def render_line_frame(img, text, cfg):
    """Draw one line of text following the style (placement, letter spacing, background box).

    Text that does not fit is shrunk or wrapped into two lines. Given a transparent layer (RGBA), it keeps the
    transparency (for video overlays).
    """
    img_w, img_h = cfg.img_size
    draw = ImageDraw.Draw(img)

    font, lines = fit_line_layout(draw, text, cfg)

    line_metrics = []
    for ln in lines:
        w = measure_line(draw, ln, font, cfg.letter_spacing)
        bbox = draw.textbbox((0, 0), ln, font=font)
        line_metrics.append((ln, w, bbox))

    line_gap = int(getattr(font, "size", 20) * 0.3)
    block_h = sum(b[3] - b[1] for _, _, b in line_metrics) + line_gap * (len(lines) - 1)

    if cfg.position == "top":
        block_top = cfg.margin_v
    elif cfg.position == "center":
        block_top = (img_h - block_h) / 2
    else:
        block_top = img_h - block_h - cfg.margin_v

    # draw.text puts the glyph top at y + bbox[1], so offset by bbox[1]
    placements = []
    cur_top = block_top
    for ln, w, bbox in line_metrics:
        placements.append((ln, w, (img_w - w) / 2, cur_top - bbox[1]))
        cur_top += (bbox[3] - bbox[1]) + line_gap

    if cfg.box:
        rgba, pad = cfg.box
        widest = max(w for _, w, _ in line_metrics)
        bx0 = (img_w - widest) / 2 - pad
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rectangle(
            [bx0, block_top - pad, bx0 + widest + 2 * pad, block_top + block_h + pad],
            fill=rgba,
        )
        orig_mode = img.mode
        img = Image.alpha_composite(img.convert("RGBA"), overlay)
        if orig_mode != "RGBA":
            img = img.convert(orig_mode)
        draw = ImageDraw.Draw(img)

    for ln, _w, x, y in placements:
        if cfg.letter_spacing > 0:
            cur_x = x
            for ch in ln:
                draw.text(
                    (cur_x, y), ch, font=font, fill=cfg.primary,
                    stroke_width=cfg.outline, stroke_fill=cfg.outline_color,
                )
                cur_x += draw.textlength(ch, font=font) + cfg.letter_spacing
        else:
            draw.text(
                (x, y), ln, font=font, fill=cfg.primary,
                stroke_width=cfg.outline, stroke_fill=cfg.outline_color,
            )
    return img


def render_frame(img_path, evt, elapsed_cs, img_cache, cfg):
    """Draw the subtitle for that time onto one frame's background image.

    Karaoke mode colours each \\k segment with the highlight or waiting colour; line mode hands the whole line
    to render_line_frame.
    """
    img = get_base_img(img_path, img_cache)
    if evt is None:
        return img

    segs = parse_karaoke(evt[2])
    if not segs:
        return img

    if cfg.mode == "line":
        full_text = "".join(t for _, _, t in segs)
        return render_line_frame(img, full_text, cfg)

    img_w, img_h = cfg.img_size
    n_highlighted = highlighted_count(segs, elapsed_cs)

    draw = ImageDraw.Draw(img)
    font, seg_lines = fit_karaoke_layout(draw, segs, cfg)

    line_texts = ["".join(t for _, _, t in line) for line in seg_lines]
    line_bboxes = [draw.textbbox((0, 0), txt, font=font) for txt in line_texts]

    line_gap = int(getattr(font, "size", 20) * 0.3)
    block_h = sum(b[3] - b[1] for b in line_bboxes) + line_gap * (len(seg_lines) - 1)
    y = img_h - block_h - cfg.margin_v

    idx = 0
    for line, _txt, bbox in zip(seg_lines, line_texts, line_bboxes, strict=False):
        total_w = bbox[2] - bbox[0]
        cur_x = (img_w - total_w) // 2
        for _s, _e, seg_text in line:
            color = cfg.primary if idx < n_highlighted else cfg.secondary
            draw.text(
                (cur_x, y),
                seg_text,
                font=font,
                fill=color,
                stroke_width=cfg.outline,
                stroke_fill=cfg.outline_color,
            )
            seg_bbox = draw.textbbox((0, 0), seg_text, font=font)
            cur_x += seg_bbox[2] - seg_bbox[0]
            idx += 1
        y += (bbox[3] - bbox[1]) + line_gap

    return img


def collect_transition_times(events, total_dur, include_char_transitions=True):
    """Collect every time at which the rendered result may change.

    Line mode has no per-character highlight, so only line starts and ends are collected.
    """
    transitions = {0.0, total_dur}
    for start, end, text in events:
        transitions.add(start)
        transitions.add(end)
        if not include_char_transitions:
            continue
        for seg_cs_s, seg_cs_e, _ in parse_karaoke(text):
            transitions.add(start + seg_cs_s / 100.0)
            transitions.add(start + seg_cs_e / 100.0)
    return sorted(transitions)


def build_concat_lines(
    transitions,
    timeline,
    events,
    cfg,
    frames_dir,
    img_cache=None,
):
    """Draw a frame for each span where the state changes, and return lines for ffmpeg concat.

    Spans with the same state key (background image, subtitle event, highlighted characters) reuse an
    already drawn frame.
    """
    if img_cache is None:
        img_cache = {}
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
        img_file = img_at(timeline, mid)
        evt = event_at(events, mid)

        elapsed_cs = (mid - evt[0]) * 100 if evt else 0
        n_hl = 0
        if evt and cfg.mode == "karaoke":
            n_hl = highlighted_count(parse_karaoke(evt[2]), elapsed_cs)

        state_key = (img_file, evt[0] if evt else None, n_hl)

        if state_key not in rendered_cache:
            base = os.path.splitext(os.path.basename(img_file))[0]
            evt_start = str(evt[0] if evt else "x").replace(".", "_")
            frame_path = os.path.join(frames_dir, f"frame_{base}_{evt_start}_{n_hl}.png")
            img = render_frame(img_file, evt, elapsed_cs, img_cache, cfg)
            img.save(frame_path)
            rendered_cache[state_key] = frame_path
            print(f"  Rendered: {os.path.basename(frame_path)}")

        frame_path = rendered_cache[state_key]
        last_frame_path = frame_path
        concat_lines.append(f"file '{os.path.abspath(frame_path)}'")
        concat_lines.append(f"duration {dur:.6f}")

    if last_frame_path:
        concat_lines.append(f"file '{os.path.abspath(last_frame_path)}'")

    return concat_lines


def run_ffmpeg(concat_txt, audio, output):
    """Encode the drawn frames and the audio into an MP4."""
    print("\nRunning ffmpeg...")
    return subprocess.run(
        [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", concat_txt,
            "-i", audio,
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            output,
        ],
    )


def load_style_file(path):
    """Load the style JSON and return a dict with invalid values warned about and removed."""
    with open(path, encoding="utf-8") as f:
        style = json.load(f)
    if not isinstance(style, dict):
        raise SystemExit(f"Error: the style file must be a JSON object: {path}")

    if style.get("mode") not in (None, "karaoke", "line"):
        print(f"Warning: unknown mode '{style['mode']}' → using karaoke", file=sys.stderr)
        style.pop("mode")
    if style.get("position") not in (None, "top", "center", "bottom"):
        print(f"Warning: unknown position '{style['position']}' → using bottom", file=sys.stderr)
        style.pop("position")
    for key in ("primary_color", "secondary_color", "outline_color"):
        v = style.get(key)
        if v is not None and not (isinstance(v, str) and HEX_COLOR_RE.fullmatch(v)):
            print(f"Warning: {key} must be '#RRGGBB' ({v!r}) → using the ASS value", file=sys.stderr)
            style.pop(key)
    font = style.get("font")
    if font is not None and not os.path.exists(font):
        print(f"Warning: font in the style not found: {font} → using the default search order", file=sys.stderr)
        style.pop("font")
    index = style.get("font_index")
    if index is not None and not (isinstance(index, int) and not isinstance(index, bool) and index >= 0):
        print(f"Warning: font_index must be an integer of 0 or more ({index!r}) → using 0", file=sys.stderr)
        style.pop("font_index")
    return style


def pick_ass_style(subs, source=""):
    """Pick the ASS style to render from (the Karaoke style first)."""
    style = subs.styles.get("Karaoke")
    if style is not None:
        return style
    if not subs.styles:
        raise SystemExit(f"Error: the ASS has no style definitions: {source or '(subtitles)'}")
    return next(iter(subs.styles.values()))


def _parse_box(style_over, scale):
    """Convert the style JSON's box into ((r, g, b, a), pad_px). None if invalid."""
    box_over = style_over.get("box")
    if not isinstance(box_over, dict):
        return None
    box_color = box_over.get("color", "#000000")
    if not (isinstance(box_color, str) and HEX_COLOR_RE.fullmatch(box_color)):
        box_color = "#000000"
    alpha = min(1.0, max(0.0, float(box_over.get("alpha", 0.4))))
    pad = int(float(box_over.get("pad", 16)) * scale)
    return ((*hex_to_rgb(box_color), int(alpha * 255)), pad)


def build_render_config(subs, img_size, style_over, force_mode=None, subs_source=""):
    """Build a RenderConfig from the ASS style and the style.json overrides.

    Pixel values (font_size / margin_v / outline / letter_spacing / box.pad) are taken as given for the
    ASS PlayResY and scaled to the real image or video height. burn_subs.py (slideshow) and
    burn_subs_video.py (video overlay) share this function so both look the same.
    force_mode ignores style.json's mode and fixes it (the video side supports line mode only).
    """
    img_w, img_h = img_size
    play_res_y = int(subs.info.get("PlayResY", "1080"))
    scale = img_h / play_res_y

    ass_style = pick_ass_style(subs, subs_source)
    font_size = int(ass_style.fontsize * scale)
    margin_v = int(ass_style.marginv * scale)
    outline = max(1, int(ass_style.outline * scale))
    primary = color_to_rgb(ass_style.primarycolor)
    secondary = color_to_rgb(ass_style.secondarycolor)
    outline_color = color_to_rgb(ass_style.outlinecolor)

    if "font_size" in style_over:
        font_size = int(float(style_over["font_size"]) * scale)
    if "margin_v" in style_over:
        margin_v = int(float(style_over["margin_v"]) * scale)
    if "outline" in style_over:
        outline = max(0, int(float(style_over["outline"]) * scale))
    if "primary_color" in style_over:
        primary = hex_to_rgb(style_over["primary_color"])
    if "secondary_color" in style_over:
        secondary = hex_to_rgb(style_over["secondary_color"])
    if "outline_color" in style_over:
        outline_color = hex_to_rgb(style_over["outline_color"])

    mode = force_mode or style_over.get("mode", "karaoke")
    print(f"Mode: {mode}, Font size: {font_size}, MarginV: {margin_v}, Outline: {outline}")
    print(f"Primary: {primary}, Secondary: {secondary}")

    if "font" in style_over:
        # A ttc holds several weights in one file (e.g. Hiragino Mincho ProN: 0 = W3 / 2 = W6).
        # Pick one with font_index only when something other than the default 0 is wanted.
        font_index = int(style_over.get("font_index", 0))
        font = ImageFont.truetype(style_over["font"], font_size, index=font_index)
        print(f"Font: {style_over['font']} (index {font_index})")
    else:
        font = load_font(font_size)

    return RenderConfig(
        font=font,
        img_size=(img_w, img_h),
        margin_v=margin_v,
        outline=outline,
        primary=primary,
        secondary=secondary,
        outline_color=outline_color,
        mode=mode,
        position=style_over.get("position", "bottom"),
        letter_spacing=int(float(style_over.get("letter_spacing", 0)) * scale),
        box=_parse_box(style_over, scale),
    )


def extract_keyframes(zip_path, work_dir):
    """Extract keyframes.zip and return the list of (image file name, seconds shown).

    If the ZIP is not as expected, it stops with a message that names the cause instead of a traceback.
    """
    print(f"Extracting {zip_path} -> {work_dir}")
    try:
        with zipfile.ZipFile(zip_path) as z:
            safe_extract(z, work_dir)
    except FileNotFoundError:
        raise SystemExit(f"Error: keyframes ZIP not found: {zip_path}") from None
    except zipfile.BadZipFile:
        raise SystemExit(f"Error: cannot read the keyframes ZIP (damaged or not a ZIP): {zip_path}") from None

    inputs_txt = os.path.join(work_dir, "inputs.txt")
    if not os.path.exists(inputs_txt):
        raise SystemExit(f"Error: the ZIP has no inputs.txt: {zip_path}")

    images_with_durations = read_images_with_durations(inputs_txt)
    if not images_with_durations:
        raise SystemExit(f"Error: inputs.txt has no image entries: {zip_path}")

    missing = [name for name, _ in images_with_durations if not os.path.exists(os.path.join(work_dir, name))]
    if missing:
        raise SystemExit(f"Error: images listed in inputs.txt are missing from the ZIP: {', '.join(missing[:5])}")
    return images_with_durations


def resolve_subtitles(work_dir, subs_override, keyframes):
    """Decide which ASS to use (--subs first, otherwise the subtitles.ass in the ZIP)."""
    if subs_override:
        return subs_override
    path = os.path.join(work_dir, "subtitles.ass")
    if not os.path.exists(path):
        raise SystemExit(
            f"Error: the ZIP has no subtitles.ass: {keyframes}\n"
            "       Pass a timed ASS with --subs."
        )
    return path


def main():
    """Read the inputs, draw the subtitle frames and write the MP4."""
    parser = argparse.ArgumentParser(description="Burn ASS karaoke subtitles onto keyframe images and produce an MP4.")
    parser.add_argument("audio", help="audio used as the soundtrack (mp3 / wav etc.)")
    parser.add_argument("keyframes", help="keyframe ZIP (PNG + inputs.txt + subtitles.ass)")
    parser.add_argument("output", nargs="?", default="output.mp4", help="output MP4 (default: output.mp4)")
    parser.add_argument("--subs", dest="subs_override", help="ASS file that replaces the subtitles in the ZIP")
    parser.add_argument("--style-file", help="JSON file that overrides the drawing style (presets in styles/)")
    args = parser.parse_args()

    require_ffmpeg("ffmpeg")
    if not os.path.exists(args.audio):
        raise SystemExit(f"Error: audio file not found: {args.audio}")
    if args.subs_override and not os.path.exists(args.subs_override):
        raise SystemExit(f"Error: subtitle file not found: {args.subs_override}")

    style_over = load_style_file(args.style_file) if args.style_file else {}

    with tempfile.TemporaryDirectory(prefix="burn_subs_") as work_dir:
        images_with_durations = extract_keyframes(args.keyframes, work_dir)
        print(f"Images: {[f for f, _ in images_with_durations]}")

        first_img_path = os.path.join(work_dir, images_with_durations[0][0])
        with Image.open(first_img_path) as probe:
            img_size = probe.size
        print(f"Image size: {img_size[0]}x{img_size[1]}")

        subtitle_file = resolve_subtitles(work_dir, args.subs_override, args.keyframes)
        subs_raw = pysubs2.load(subtitle_file)
        cfg = build_render_config(subs_raw, img_size, style_over, subs_source=subtitle_file)

        timeline, total_dur = build_timeline(work_dir, images_with_durations)
        events = [(e.start / 1000.0, e.end / 1000.0, e.text) for e in subs_raw]
        transitions = collect_transition_times(
            events, total_dur, include_char_transitions=(cfg.mode == "karaoke"),
        )
        print(f"Total transition segments: {len(transitions) - 1}")

        frames_dir = os.path.join(work_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)
        concat_lines = build_concat_lines(transitions, timeline, events, cfg, frames_dir)

        concat_txt = os.path.join(frames_dir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(concat_lines))

        result = run_ffmpeg(concat_txt, args.audio, args.output)

    if result.returncode == 0:
        size_mb = os.path.getsize(args.output) / 1024 / 1024
        print(f"Done! {args.output} ({size_mb:.1f} MB)")
    else:
        print(f"ffmpeg failed (exit code {result.returncode})")
        sys.exit(1)


if __name__ == "__main__":
    main()
