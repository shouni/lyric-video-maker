#!/usr/bin/env python3
"""完成済み動画（MV）に1行スタイル字幕を焼き込む。

burn_subs.py がキーフレーム静止画のスライドショーを生成するのに対し、
こちらは動画の映像・カメラワークを保ったまま、行ごとの字幕を
透過PNG + ffmpeg overlay の時間指定合成で乗せる（line モード専用）。

Usage:
    python3 burn_subs_video.py <video.mp4> <subtitles.ass> [output.mp4] [--style-file styles/rock.json]

字幕は align_subtitles.py の出力 ASS（\\k タグは無視し、行の開始/終了時刻のみ使用）。
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image
import pysubs2

from burn_subs import (
    RenderConfig,
    color_to_rgb,
    hex_to_rgb,
    load_font,
    load_style_file,
    render_line_frame,
    HEX_COLOR_RE,
)


def probe_size(video):
    """ffprobe で動画の解像度を取得する。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def build_windows(subs):
    """ASS イベントを (開始, 終了, テキスト) の重ならない表示区間に変換する。

    行が重なる場合は burn_subs の event_at と同じ「先の行が優先」で解消する。
    """
    events = []
    for e in subs:
        plain = re.sub(r"\{[^}]*\}", "", e.text)
        plain = re.sub(r"\\[nNh]", "", plain).strip()
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
    parser = argparse.ArgumentParser(description="動画に1行スタイル字幕を焼き込む。")
    parser.add_argument("video", help="入力動画 (mp4)")
    parser.add_argument("subtitles", help="タイミング付き ASS ファイル")
    parser.add_argument("output", nargs="?", default="output_video.mp4", help="出力 MP4")
    parser.add_argument("--style-file", help="描画スタイルの JSON（styles/ にプリセットあり）")
    args = parser.parse_args()

    for path in (args.video, args.subtitles):
        if not os.path.exists(path):
            sys.exit(f"Error: ファイルが見つかりません: {path}")

    style_over = load_style_file(args.style_file) if args.style_file else {}
    if style_over.get("mode") == "karaoke":
        print("Warning: 動画焼き込みは line モード専用のため mode=karaoke は無視します", file=sys.stderr)

    subs = pysubs2.load(args.subtitles)
    img_w, img_h = probe_size(args.video)
    play_res_y = int(subs.info.get("PlayResY", "1080"))
    scale = img_h / play_res_y
    print(f"Video size: {img_w}x{img_h} (scale {scale:.2f})")

    ass_style = subs.styles.get("Karaoke") or list(subs.styles.values())[0]
    font_size = int(ass_style.fontsize * scale)
    margin_v = int(ass_style.marginv * scale)
    outline = max(1, int(ass_style.outline * scale))
    primary = color_to_rgb(ass_style.primarycolor)
    outline_color = color_to_rgb(ass_style.outlinecolor)

    if "font_size" in style_over:
        font_size = int(float(style_over["font_size"]) * scale)
    if "margin_v" in style_over:
        margin_v = int(float(style_over["margin_v"]) * scale)
    if "outline" in style_over:
        outline = max(0, int(float(style_over["outline"]) * scale))
    if "primary_color" in style_over:
        primary = hex_to_rgb(style_over["primary_color"])
    if "outline_color" in style_over:
        outline_color = hex_to_rgb(style_over["outline_color"])
    box = None
    box_over = style_over.get("box")
    if isinstance(box_over, dict):
        box_color = box_over.get("color", "#000000")
        if not (isinstance(box_color, str) and HEX_COLOR_RE.fullmatch(box_color)):
            box_color = "#000000"
        alpha = min(1.0, max(0.0, float(box_over.get("alpha", 0.4))))
        pad = int(float(box_over.get("pad", 16)) * scale)
        box = ((*hex_to_rgb(box_color), int(alpha * 255)), pad)

    from PIL import ImageFont
    if "font" in style_over:
        font = ImageFont.truetype(style_over["font"], font_size)
        print(f"Font: {style_over['font']}")
    else:
        font = load_font(font_size)

    cfg = RenderConfig(
        font=font,
        img_size=(img_w, img_h),
        margin_v=margin_v,
        outline=outline,
        primary=primary,
        secondary=primary,
        outline_color=outline_color,
        mode="line",
        position=style_over.get("position", "bottom"),
        letter_spacing=int(float(style_over.get("letter_spacing", 0)) * scale),
        box=box,
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
