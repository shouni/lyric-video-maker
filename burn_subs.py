#!/usr/bin/env python3
"""Burn ASS karaoke subtitles onto PNG images and create MP4.

Usage:
    python3 burn_subs.py <audio.mp3> <keyframes.zip> [output.mp4] [--subs <subtitles.ass>] [--style-file <style.json>]

--style-file で描画スタイルを JSON で上書きできる（styles/ にプリセットあり）。
mode="line" にするとカラオケハイライトなしの1行表示になる。
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
    position: str = "bottom"  # "top" | "center" | "bottom"（line モードのみ）
    letter_spacing: int = 0  # 字間 px（line モードのみ）
    box: Any = None  # ((r, g, b, a), pad_px) の背景座布団。None で無効


HEX_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


def hex_to_rgb(value):
    """'#RRGGBB' を RGB タプルへ変換する。"""
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def require_ffmpeg(*tools):
    """必要な外部コマンドの存在を確認する。無ければ導入方法を添えて中断する。

    ffmpeg 不在時に subprocess の FileNotFoundError がそのまま出ると原因が分かりにくいため、
    処理を始める前に確認する。
    """
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise SystemExit(f"Error: {', '.join(missing)} が見つかりません（macOS: brew install ffmpeg）")


FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
    os.path.expanduser("~/Library/Fonts/SourceHanSans-VF.otf.ttc"),
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


def safe_extract(zip_file, target_dir):
    """Extract zip contents, rejecting any path that escapes target_dir."""
    abs_target = os.path.abspath(target_dir) + os.sep
    for member in zip_file.infolist():
        dest = os.path.abspath(os.path.join(abs_target, member.filename))
        if not dest.startswith(abs_target):
            raise ValueError(f"Attempted path traversal in zip: {member.filename}")
    zip_file.extractall(target_dir)


def color_to_rgb(c):
    """Convert a pysubs2 ASS color object to a PIL-compatible RGB tuple."""
    return (c.r, c.g, c.b)


def parse_karaoke(raw_text):
    """Parse ASS karaoke \\k tags into centisecond text segments."""
    segments = []
    cs = 0
    for m in re.finditer(r'\{\\k(\d+)\}([^{]*)', raw_text):
        k = int(m.group(1))
        text = re.sub(r'\\[nNh]', '', m.group(2))
        segments.append((cs, cs + k, text))
        cs += k
    return segments


def read_images_with_durations(inputs_txt):
    """Read ffmpeg concat file entries as image filename and duration pairs."""
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
    """Build absolute image time ranges from concat image durations."""
    timeline = []
    t = 0.0
    for img, dur in images_with_durations:
        timeline.append((t, t + dur, os.path.join(work_dir, img)))
        t += dur
    return timeline, t


def img_at(timeline, t_sec):
    """Return the image path active at the given time in seconds."""
    for start, end, img in timeline:
        if start <= t_sec < end:
            return img
    return timeline[-1][2]


def event_at(events, t_sec):
    """Return the subtitle event active at the given time in seconds."""
    for start, end, text in events:
        if start <= t_sec < end:
            return (start, end, text)
    return None


def load_font(font_size):
    """Load a CJK-capable font for rendering Japanese karaoke text."""
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
    """Return a copy of a cached RGB source image."""
    if path not in img_cache:
        img_cache[path] = Image.open(path).convert("RGB")
    return img_cache[path].copy()


LINE_MAX_WIDTH_RATIO = 0.92  # 1行が占めてよい画像幅の上限比率
LINE_SHRINK_FLOOR = 0.7  # 1行のまま縮小してよい下限比。これ未満に縮むなら2行に折り返す
MIN_FONT_SIZE = 12  # 自動縮小の下限（これ以上小さくすると判読できない）


def measure_line(draw, text, font, spacing):
    """字間込みの1行の描画幅を返す。"""
    if spacing > 0:
        return sum(draw.textlength(ch, font=font) for ch in text) + spacing * max(0, len(text) - 1)
    return draw.textlength(text, font=font)


def best_split(draw, text, font, spacing):
    """2行分割の候補から、長い方の実測幅が最小になる分割点を返す。

    スペースがあればスペース位置のみを候補にする（英語歌詞の単語を割らない）。
    無ければ全文字境界を候補にする（日本語歌詞）。分割できない場合は ("text", "")。
    """
    candidates = [i + 1 for i, ch in enumerate(text[:-1]) if ch == " "]
    if not candidates:
        candidates = list(range(1, len(text)))
    best = (text, "")
    best_w = None
    for idx in candidates:
        l1 = text[:idx].strip()
        l2 = text[idx:].strip()
        if not l1 or not l2:
            continue
        w = max(measure_line(draw, l1, font, spacing), measure_line(draw, l2, font, spacing))
        if best_w is None or w < best_w:
            best_w = w
            best = (l1, l2)
    return best


def shrink_to_fit(draw, lines, font, spacing, max_w):
    """指定した行群が max_w に収まるまでフォントを縮小する。

    MIN_FONT_SIZE まで縮めても収まらない場合はそのサイズで諦める（読めない字幕を出すより
    はみ出しを許容する）。返り値のフォントとサイズは常に一致する。
    """
    size = getattr(font, "size", 0) or 0
    if size <= MIN_FONT_SIZE:
        return font, size
    while True:
        f = font.font_variant(size=size)
        if size <= MIN_FONT_SIZE or all(measure_line(draw, ln, f, spacing) <= max_w for ln in lines):
            return f, size
        size -= 2


def fit_line_layout(draw, text, cfg):
    """テキストが画像幅に収まるフォントと行リスト（1〜2行）を決める。

    まず1行のまま縮小を試み、縮小率が LINE_SHRINK_FLOOR を下回るほど長い場合は
    2行に折り返してから収まるサイズへ縮小する（ap-comp の fitTitle と同じ方針）。
    """
    max_w = cfg.img_size[0] * LINE_MAX_WIDTH_RATIO
    font = cfg.font
    base_size = getattr(font, "size", 0) or 0
    if base_size <= 0 or measure_line(draw, text, font, cfg.letter_spacing) <= max_w:
        return font, [text]

    _, size_one_line = shrink_to_fit(draw, [text], font, cfg.letter_spacing, max_w)
    if size_one_line >= base_size * LINE_SHRINK_FLOOR:
        return font.font_variant(size=size_one_line), [text]

    l1, l2 = best_split(draw, text, font, cfg.letter_spacing)
    if not l2:
        return font.font_variant(size=size_one_line), [text]
    fitted, _ = shrink_to_fit(draw, [l1, l2], font, cfg.letter_spacing, max_w)
    return fitted, [l1, l2]


def best_split_segs(draw, segs, font, spacing):
    """カラオケ \\k セグメント列を2行に分割する分割点を返す（best_split のセグメント版）。

    セグメント内部では割らない（既存ASSは句読点や語末スペースが直前セグメントに
    吸収されているため、セグメント境界＝best_split の文字/スペース境界に対応する）。
    """
    texts = [t for _, _, t in segs]
    candidates = [i + 1 for i, t in enumerate(texts[:-1]) if t.endswith(" ")]
    if not candidates:
        candidates = list(range(1, len(segs)))
    best = (segs, [])
    best_w = None
    for idx in candidates:
        segs1, segs2 = segs[:idx], segs[idx:]
        text1 = "".join(texts[:idx]).strip()
        text2 = "".join(texts[idx:]).strip()
        if not text1 or not text2:
            continue
        w = max(measure_line(draw, text1, font, spacing), measure_line(draw, text2, font, spacing))
        if best_w is None or w < best_w:
            best_w = w
            best = (segs1, segs2)
    return best


def fit_karaoke_layout(draw, segs, cfg):
    """カラオケ \\k セグメント列が画像幅に収まるフォントと行分割（1〜2行）を決める。

    方針は fit_line_layout と同じ（先に縮小、それでも収まらなければ2行に折り返す）。
    """
    max_w = cfg.img_size[0] * LINE_MAX_WIDTH_RATIO
    font = cfg.font
    base_size = getattr(font, "size", 0) or 0
    full_text = "".join(t for _, _, t in segs)
    if base_size <= 0 or measure_line(draw, full_text, font, 0) <= max_w:
        return font, [segs]

    _, size_one_line = shrink_to_fit(draw, [full_text], font, 0, max_w)
    if size_one_line >= base_size * LINE_SHRINK_FLOOR:
        return font.font_variant(size=size_one_line), [segs]

    segs1, segs2 = best_split_segs(draw, segs, font, 0)
    if not segs2:
        return font.font_variant(size=size_one_line), [segs]
    line_texts = ["".join(t for _, _, t in segs1), "".join(t for _, _, t in segs2)]
    fitted, _ = shrink_to_fit(draw, line_texts, font, 0, max_w)
    return fitted, [segs1, segs2]


def render_line_frame(img, text, cfg):
    """1行テキストをスタイル設定（配置・字間・座布団）に従って描画する。

    幅に収まらないテキストはフォント縮小または2行折り返しで調整する。
    透過レイヤー（RGBA）を渡された場合は透過を保ったまま返す（動画オーバーレイ用）。
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

    # draw.text の y はグリフ上端が y+bbox[1] に来るため bbox[1] 分ずらす
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
    """Render the active karaoke line onto one image frame."""
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
    n_highlighted = sum(1 for (s, _e, _text) in segs if elapsed_cs >= s)

    draw = ImageDraw.Draw(img)
    font, seg_lines = fit_karaoke_layout(draw, segs, cfg)

    line_texts = ["".join(t for _, _, t in line) for line in seg_lines]
    line_bboxes = [draw.textbbox((0, 0), txt, font=font) for txt in line_texts]

    line_gap = int(getattr(font, "size", 20) * 0.3)
    block_h = sum(b[3] - b[1] for b in line_bboxes) + line_gap * (len(seg_lines) - 1)
    y = img_h - block_h - cfg.margin_v

    idx = 0
    for line, txt, bbox in zip(seg_lines, line_texts, line_bboxes):
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
    """Collect every time where the rendered subtitle state can change.

    line モードでは文字単位のハイライトが無いため、行の開始/終了のみ集める。
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
    """Render unique subtitle states and return ffmpeg concat file lines."""
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
            segs = parse_karaoke(evt[2])
            n_hl = sum(1 for (s, _e, _text) in segs if elapsed_cs >= s)

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
    """Encode rendered frames and audio into the final MP4."""
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
    """スタイル JSON を読み込み、不正値は警告して除外した dict を返す。"""
    with open(path, encoding="utf-8") as f:
        style = json.load(f)
    if not isinstance(style, dict):
        raise SystemExit(f"Error: スタイルファイルは JSON オブジェクトである必要があります: {path}")

    if style.get("mode") not in (None, "karaoke", "line"):
        print(f"Warning: 不明な mode '{style['mode']}' → karaoke を使用", file=sys.stderr)
        style.pop("mode")
    if style.get("position") not in (None, "top", "center", "bottom"):
        print(f"Warning: 不明な position '{style['position']}' → bottom を使用", file=sys.stderr)
        style.pop("position")
    for key in ("primary_color", "secondary_color", "outline_color"):
        v = style.get(key)
        if v is not None and not (isinstance(v, str) and HEX_COLOR_RE.fullmatch(v)):
            print(f"Warning: {key} は '#RRGGBB' 形式で指定してください（{v!r}）→ ASS の値を使用", file=sys.stderr)
            style.pop(key)
    font = style.get("font")
    if font is not None and not os.path.exists(font):
        print(f"Warning: スタイル指定のフォントが見つかりません: {font} → 既定の探索順を使用", file=sys.stderr)
        style.pop("font")
    return style


def pick_ass_style(subs, source=""):
    """描画の土台にする ASS スタイルを選ぶ（Karaoke スタイル優先）。"""
    style = subs.styles.get("Karaoke")
    if style is not None:
        return style
    if not subs.styles:
        raise SystemExit(f"Error: ASS にスタイル定義がありません: {source or '(subtitles)'}")
    return next(iter(subs.styles.values()))


def _parse_box(style_over, scale):
    """スタイル JSON の box 指定を ((r, g, b, a), pad_px) へ変換する。無効なら None。"""
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
    """ASS スタイルと style.json の上書きから RenderConfig を組み立てる。

    px 値（font_size / margin_v / outline / letter_spacing / box.pad）は ASS の PlayResY 基準で
    指定されている前提で、実際の画像・動画の高さへスケールする。burn_subs.py（スライドショー）と
    burn_subs_video.py（動画オーバーレイ）で同じ見た目になるよう、両者でこの関数を共有する。
    force_mode を渡すと style.json の mode 指定を無視して固定する（動画側は line モード専用）。
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
        font = ImageFont.truetype(style_over["font"], font_size)
        print(f"Font: {style_over['font']}")
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
    """keyframes.zip を展開し、(画像ファイル名, 表示秒数) の並びを返す。

    ZIP の中身が想定と違う場合はトレースバックではなく原因の分かるメッセージで中断する。
    """
    print(f"Extracting {zip_path} -> {work_dir}")
    try:
        with zipfile.ZipFile(zip_path) as z:
            safe_extract(z, work_dir)
    except FileNotFoundError:
        raise SystemExit(f"Error: keyframes ZIP が見つかりません: {zip_path}") from None
    except zipfile.BadZipFile:
        raise SystemExit(f"Error: keyframes ZIP を読み込めません（壊れているか ZIP ではありません）: {zip_path}") from None

    inputs_txt = os.path.join(work_dir, "inputs.txt")
    if not os.path.exists(inputs_txt):
        raise SystemExit(f"Error: ZIP に inputs.txt が含まれていません: {zip_path}")

    images_with_durations = read_images_with_durations(inputs_txt)
    if not images_with_durations:
        raise SystemExit(f"Error: inputs.txt に画像エントリがありません: {zip_path}")

    missing = [name for name, _ in images_with_durations if not os.path.exists(os.path.join(work_dir, name))]
    if missing:
        raise SystemExit(f"Error: inputs.txt が参照する画像が ZIP にありません: {', '.join(missing[:5])}")
    return images_with_durations


def resolve_subtitles(work_dir, subs_override, keyframes):
    """使用する ASS のパスを決める（--subs 優先、無ければ ZIP 内の subtitles.ass）。"""
    if subs_override:
        return subs_override
    path = os.path.join(work_dir, "subtitles.ass")
    if not os.path.exists(path):
        raise SystemExit(
            f"Error: ZIP に subtitles.ass が含まれていません: {keyframes}\n"
            "       --subs でタイミング付き ASS を指定してください。"
        )
    return path


def main():
    """Load inputs, render karaoke subtitle frames, and encode the output video."""
    parser = argparse.ArgumentParser(description="Burn ASS karaoke subtitles onto PNG images and create MP4.")
    parser.add_argument("audio", help="Input audio file (mp3)")
    parser.add_argument("keyframes", help="Input keyframes zip file")
    parser.add_argument("output", nargs="?", default="output.mp4", help="Output MP4 file")
    parser.add_argument("--subs", dest="subs_override", help="Override subtitles file (ass)")
    parser.add_argument("--style-file", help="描画スタイルを上書きする JSON ファイル（styles/ にプリセットあり）")
    args = parser.parse_args()

    require_ffmpeg("ffmpeg")
    if not os.path.exists(args.audio):
        raise SystemExit(f"Error: 音声ファイルが見つかりません: {args.audio}")
    if args.subs_override and not os.path.exists(args.subs_override):
        raise SystemExit(f"Error: 指定された字幕ファイルが見つかりません: {args.subs_override}")

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
