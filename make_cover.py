#!/usr/bin/env python3
"""Create titled cover images from an MP4 frame (the first frame by default, any time with --at).

Writes two images: regular YouTube (16:9, 1280x720) and Shorts (9:16, 1080x1920).
The Shorts cover puts the 16:9 frame over a blurred background with the title below it.
Fonts are found with the same logic as burn_subs.py (load_font).

Usage:
    python3 make_cover.py "/path/to/video_with_lyrics.mp4" --title "Packet Loss" [--at 8]

Without a title, the MP4's parent folder name is used. Output goes to the MP4's folder by default.
"""

import argparse
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter

from burn_subs import load_font, require_ffmpeg

DEFAULT_ARTIST = "Digital Armor Style"

# white title + dark stroke + shadow; artist name in gold
TITLE_COLOR = (255, 255, 255)
ARTIST_COLOR = (255, 215, 0)
STROKE_COLOR = (26, 26, 26)
SHADOW_COLOR = (0, 0, 0, 166)
FONT_SIZE_RATIO = 0.075  # title font size relative to image width
ARTIST_SIZE_RATIO = 0.55  # artist name size relative to the title
MAX_WIDTH_RATIO = 0.92  # widest share of the image a title line may take
REFERENCE_WIDTH = 1024.0  # reference image width for stroke width and shadow offset


def extract_frame(video_path, at_sec=0.0):
    """Extract the frame at the given second as PNG with ffmpeg and return it as a PIL Image.

    -ss goes on the output side, not the input: burn_subs.py's output holds one frame for seconds and has a
    keyframe only at the start, so input-side seeking picks a different frame.
    """
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = os.path.join(tmp, "frame.png")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path]
        if at_sec > 0:
            cmd += ["-ss", str(at_sec)]
        cmd += ["-frames:v", "1", "-q:v", "2", frame_path]
        subprocess.run(cmd, check=True)
        if not os.path.exists(frame_path):
            sys.exit(f"error: could not get the frame at {at_sec} s (is it past the end of the video?): {video_path}")
        return Image.open(frame_path).convert("RGB")


def fit_font(draw, text, base_size, max_width):
    """Return the font at the largest size (up to base_size) that fits max_width."""
    size = int(base_size)
    font = load_font(size)
    width = draw.textlength(text, font=font)
    if width > max_width and width > 0:
        size = max(12, int(base_size * max_width / width))
        font = load_font(size)
    return font, size


def draw_styled_text(img, text, center_x, center_y, font, fill, scale):
    """Draw text as a shadow, then the stroked text."""
    stroke_w = max(1, round(2 * scale))
    shadow_offset = max(1, round(2 * scale))

    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow_layer).text(
        (center_x + shadow_offset, center_y + shadow_offset),
        text, font=font, fill=SHADOW_COLOR, anchor="mm",
    )
    img.alpha_composite(shadow_layer)

    ImageDraw.Draw(img).text(
        (center_x, center_y), text, font=font, fill=fill, anchor="mm",
        stroke_width=stroke_w, stroke_fill=STROKE_COLOR,
    )


def draw_title_block(img, title, artist, center_y=None, artist_color=ARTIST_COLOR):
    """Draw the title and artist name. Without center_y they sit near the bottom."""
    w, h = img.size
    scale = max(1.0, w / REFERENCE_WIDTH)
    draw = ImageDraw.Draw(img)

    base_size = max(12, w * FONT_SIZE_RATIO)
    title_font, title_size = fit_font(draw, title, base_size, w * MAX_WIDTH_RATIO)

    artist_size = max(10, title_size * ARTIST_SIZE_RATIO)
    artist_font, artist_size = fit_font(draw, artist, artist_size, w * MAX_WIDTH_RATIO)

    if center_y is None:
        artist_y = h - title_size * 0.5 - artist_size * 0.5
        title_y = artist_y - title_size * 1.2
    else:
        title_y = center_y
        artist_y = title_y + title_size * 1.2

    draw_styled_text(img, title, w / 2, title_y, title_font, TITLE_COLOR, scale)
    draw_styled_text(img, artist, w / 2, artist_y, artist_font, artist_color, scale)


def parse_hex_color(text):
    """Turn "8FE3FF" / "#8FE3FF" into an RGB tuple."""
    text = text.lstrip("#")
    if len(text) != 6:
        raise argparse.ArgumentTypeError(f"expected RRGGBB hex: {text}")
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected RRGGBB hex: {text}") from None


def center_crop_to_ratio(img, ratio_w, ratio_h):
    """Crop the centre of the image to the given aspect ratio."""
    w, h = img.size
    target = ratio_w / ratio_h
    if w / h > target:
        new_w = round(h * target)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = round(w / target)
    top = (h - new_h) // 2
    return img.crop((0, top, 0 + w, top + new_h))


def make_youtube_cover(frame, title, artist, size=(1280, 720), artist_color=ARTIST_COLOR):
    """Regular 16:9 YouTube cover."""
    img = center_crop_to_ratio(frame, 16, 9).resize(size, Image.LANCZOS).convert("RGBA")
    draw_title_block(img, title, artist, artist_color=artist_color)
    return img.convert("RGB")


def make_short_cover(frame, title, artist, size=(1080, 1920), artist_color=ARTIST_COLOR):
    """9:16 Shorts cover: the 16:9 frame centred on a blurred background, title below."""
    w, h = size

    bg = center_crop_to_ratio(frame, w, h).resize(size, Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    bg = Image.eval(bg, lambda v: int(v * 0.5))  # darken so the frame and text stand out
    img = bg.convert("RGBA")

    fg_h = round(w * frame.size[1] / frame.size[0])
    fg = frame.resize((w, fg_h), Image.LANCZOS)
    fg_top = round(h * 0.42) - fg_h // 2
    img.paste(fg, (0, fg_top))

    # put the text halfway between the frame's bottom and the image's bottom
    text_center = (fg_top + fg_h + h) / 2 - w * FONT_SIZE_RATIO * 0.6
    draw_title_block(img, title, artist, center_y=text_center, artist_color=artist_color)
    return img.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="Create cover images from an MP4 frame")
    parser.add_argument("video", help="input MP4 path")
    parser.add_argument("--title", help="title (default: the MP4's parent folder name)")
    parser.add_argument("--artist", default=DEFAULT_ARTIST, help=f"artist name (default: {DEFAULT_ARTIST})")
    parser.add_argument(
        "--artist-color", type=parse_hex_color, default=ARTIST_COLOR, metavar="RRGGBB",
        help="artist name colour (default: gold FFD700)",
    )
    parser.add_argument("--outdir", help="output folder (default: the MP4's folder)")
    parser.add_argument(
        "--at", type=float, default=0.0, metavar="SEC",
        help="time of the frame used for the cover (seconds, default: 0 = first frame; set it when the video starts from black)",
    )
    args = parser.parse_args()

    require_ffmpeg("ffmpeg")
    video_path = os.path.abspath(args.video)
    if not os.path.exists(video_path):
        sys.exit(f"error: video not found: {video_path}")
    if args.at < 0:
        sys.exit(f"error: --at must be 0 or more: {args.at}")

    title = args.title or os.path.basename(os.path.dirname(video_path))
    outdir = os.path.abspath(args.outdir) if args.outdir else os.path.dirname(video_path)
    os.makedirs(outdir, exist_ok=True)

    frame = extract_frame(video_path, args.at)

    youtube = make_youtube_cover(frame, title, args.artist, artist_color=args.artist_color)
    youtube_path = os.path.join(outdir, "cover_youtube.png")
    youtube.save(youtube_path)
    print(f"wrote {youtube_path} ({youtube.size[0]}x{youtube.size[1]})")

    short = make_short_cover(frame, title, args.artist, artist_color=args.artist_color)
    short_path = os.path.join(outdir, "cover_short.png")
    short.save(short_path)
    print(f"wrote {short_path} ({short.size[0]}x{short.size[1]})")


if __name__ == "__main__":
    main()
