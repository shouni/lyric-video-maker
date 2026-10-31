"""YouTube サムネイルの背景（1280x720）: MV の描画からリング・縦線・波形だけを 1 コマ描く。

歌詞・見出し・タイムラインは入れない。曲名とアーティスト名はここでは描かず、ap-music の
overlay.ApplyTitle で visual_mode の title_style のまま合成する（カバーアートと同じ見た目にするため）。

キックのリングと打楽器の縦線が両方見える瞬間を、song.toml の [thumbnail] window の中から選ぶ。
"""
from PIL import Image, ImageDraw

from beatmv import render as r

SIZE = (1280, 720)


def pick_time(window):
    """window 内で、キックの 0.10〜0.22 秒後に打楽器が鳴った直後（リングが広がり、縦線が開いている）。"""
    lo, hi = window
    for p in r.C.perc:
        if lo < p < hi and any(0.10 < p - k < 0.22 for k in r.C.kick):
            return p + 0.08
    return (lo + hi) / 2


def background(song_dir, out_path):
    r.configure(song_dir, 1)
    t = pick_time(r.C.song.thumbnail_window)
    base = r.C.bg.copy()
    r.draw_motion(base, t, True)
    top = Image.new("RGBA", (r.C.W, r.C.H), (0, 0, 0, 0))
    r.draw_wave(ImageDraw.Draw(top), t, True)
    base.alpha_composite(top)
    base.convert("RGB").resize(SIZE, Image.LANCZOS).save(out_path)
    return t
