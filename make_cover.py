#!/usr/bin/env python3
"""MP4の最初のフレームからタイトル入りカバー画像を作成する。

通常YouTube用 (16:9, 1280x720) とショート用 (9:16, 1080x1920) の2枚を出力する。
ショート用は16:9フレームをぼかし背景の上に重ね、下部にタイトルを配置する。
フォントは burn_subs.py と同じ探索ロジック（load_font）を利用する。

Usage:
    .venv/bin/python make_cover.py "/path/to/video_with_lyrics.mp4" --title "Packet Loss"

タイトル省略時はMP4の親フォルダ名を使う。出力は既定でMP4と同じフォルダ。
"""

import argparse
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter

from burn_subs import load_font

DEFAULT_ARTIST = "Digital Armor Style"

# 白タイトル+ダークストローク+シャドウ、アーティスト名は金色
TITLE_COLOR = (255, 255, 255)
ARTIST_COLOR = (255, 215, 0)
STROKE_COLOR = (26, 26, 26)
SHADOW_COLOR = (0, 0, 0, 166)
FONT_SIZE_RATIO = 0.075  # 画像幅に対するタイトルフォントサイズ比
ARTIST_SIZE_RATIO = 0.55  # タイトルに対するアーティスト名サイズ比
MAX_WIDTH_RATIO = 0.92  # タイトル1行が占めてよい画像幅の上限比率
REFERENCE_WIDTH = 1024.0  # ストローク幅・影オフセットの基準画像幅


def extract_first_frame(video_path):
    """ffmpegでMP4の最初のフレームをPNGとして抽出し、PIL Imageで返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = os.path.join(tmp, "frame.png")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", video_path,
             "-frames:v", "1", "-q:v", "2", frame_path],
            check=True,
        )
        return Image.open(frame_path).convert("RGB")


def fit_font(draw, text, base_size, max_width):
    """max_widthに収まる最大フォントサイズ（base_sizeが上限）でフォントを返す。"""
    size = int(base_size)
    font = load_font(size)
    width = draw.textlength(text, font=font)
    if width > max_width and width > 0:
        size = max(12, int(base_size * max_width / width))
        font = load_font(size)
    return font, size


def draw_styled_text(img, text, center_x, center_y, font, fill, scale):
    """シャドウ → ストローク付き本文の順でテキストを描画する。"""
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


def draw_title_block(img, title, artist, center_y=None):
    """タイトルとアーティスト名を描画する。center_y省略時は下端寄せ。"""
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
    draw_styled_text(img, artist, w / 2, artist_y, artist_font, ARTIST_COLOR, scale)


def center_crop_to_ratio(img, ratio_w, ratio_h):
    w, h = img.size
    target = ratio_w / ratio_h
    if w / h > target:
        new_w = round(h * target)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = round(w / target)
    top = (h - new_h) // 2
    return img.crop((0, top, 0 + w, top + new_h))


def make_youtube_cover(frame, title, artist, size=(1280, 720)):
    """通常YouTube用 16:9 カバー。"""
    img = center_crop_to_ratio(frame, 16, 9).resize(size, Image.LANCZOS).convert("RGBA")
    draw_title_block(img, title, artist)
    return img.convert("RGB")


def make_short_cover(frame, title, artist, size=(1080, 1920)):
    """ショート用 9:16 カバー。ぼかし背景の中央に16:9フレーム、下部にタイトル。"""
    w, h = size

    bg = center_crop_to_ratio(frame, w, h).resize(size, Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    bg = Image.eval(bg, lambda v: int(v * 0.5))  # 前景とテキストを立たせるため減光
    img = bg.convert("RGBA")

    fg_h = round(w * frame.size[1] / frame.size[0])
    fg = frame.resize((w, fg_h), Image.LANCZOS)
    fg_top = round(h * 0.42) - fg_h // 2
    img.paste(fg, (0, fg_top))

    # テキストはフレーム下端と画像下端の中間に置く
    text_center = (fg_top + fg_h + h) / 2 - w * FONT_SIZE_RATIO * 0.6
    draw_title_block(img, title, artist, center_y=text_center)
    return img.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="MP4の最初のフレームからカバー画像を作成する")
    parser.add_argument("video", help="入力MP4のパス")
    parser.add_argument("--title", help="タイトル（省略時はMP4の親フォルダ名）")
    parser.add_argument("--artist", default=DEFAULT_ARTIST, help=f"アーティスト名（既定: {DEFAULT_ARTIST}）")
    parser.add_argument("--outdir", help="出力フォルダ（省略時はMP4と同じフォルダ）")
    args = parser.parse_args()

    video_path = os.path.abspath(args.video)
    if not os.path.exists(video_path):
        sys.exit(f"error: video not found: {video_path}")

    title = args.title or os.path.basename(os.path.dirname(video_path))
    outdir = os.path.abspath(args.outdir) if args.outdir else os.path.dirname(video_path)
    os.makedirs(outdir, exist_ok=True)

    frame = extract_first_frame(video_path)

    youtube = make_youtube_cover(frame, title, args.artist)
    youtube_path = os.path.join(outdir, "cover_youtube.png")
    youtube.save(youtube_path)
    print(f"wrote {youtube_path} ({youtube.size[0]}x{youtube.size[1]})")

    short = make_short_cover(frame, title, args.artist)
    short_path = os.path.join(outdir, "cover_short.png")
    short.save(short_path)
    print(f"wrote {short_path} ({short.size[0]}x{short.size[1]})")


if __name__ == "__main__":
    main()
