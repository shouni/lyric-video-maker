"""拍と歌詞で動くモーショングラフィックを 1 コマずつ Pillow で描き、FFmpeg で曲と合わせる。

座標・太さ・文字サイズは 1920x1080 基準で書き、scale 倍で描く（既定 2 = 3840x2160）。YouTube は
1440p 以上の投稿を高ビットレートの VP9/AV1 で配信するので、1080p で見る人にも文字の輪郭が残りやすい。

演出の制約（前回 MV でのユーザーの指摘）: 画面全体のフラッシュ・明るさの脈動・拍ズームはしない。
拍は局所的な図形（キックのリング・打楽器の縦線・拍の四角・打点のタイムライン）だけで見せる。

- 歌のない区間（song.toml の instrumental）は歌詞の位置に曲名を出し、最後の区間には credit を添える。
- 歌詞は次の行まで出し続けず、歌い終わりから最大 LINE_LINGER 秒で消す（間奏の間に前の行が残らない）。

各ワーカーは configure() で同じ設定を持つ（macOS の multiprocessing は spawn なので、モジュールの
状態は引き継がれない）。
"""
import bisect
import json
import math
import os
import random
import subprocess
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from beatmv import lyrics as lyr
from beatmv import song as songmod
from beatmv import style as stylemod
from burn_subs import require_ffmpeg

FPS = 30
FONT_BOLD = "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc"
FONT_LIGHT = "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"

WHITE = (255, 255, 255)  # 見出しのセクション名・ラベル・打楽器の縦線・次の行（配色に関わらず白）

LINE_LINGER = 1.5  # 歌い終わりから行を残す最大秒数
TL_SPAN = 2.5      # タイムラインで再生位置から左右に見せる秒数


class Ctx:
    """1 曲・1 解像度ぶんの設定と、ワーカーごとに一度だけ作る素材。"""

    def __init__(self, song, scale):
        self.song = song
        self.pal = stylemod.load(song.style)
        self.S = scale
        px = self.px
        self.W, self.H = px(1920), px(1080)
        self.LYRIC_Y = px(740)
        self.WAVE_Y = px(420)
        self.CENTER = (self.W // 2, self.WAVE_Y)
        self.RING_REACH = px(280)
        self.SNARE_HALF = px(150)
        self.SNARE_SPREAD = px(780)
        self.TL_Y = px(935)  # タイムライン 1 段目（KICK）の y。2 段目はその 44px 下
        self.TL_X0, self.TL_X1 = px(420), px(1500)
        self.PLAYHEAD_X = (self.TL_X0 + self.TL_X1) // 2

        self.lines = lyr.load_lines(song.lyrics, song.lyrics_aligner)
        with open(song.beats) as f:
            down = json.load(f)["downbeats"]
        beats = []
        for a, b in zip(down, down[1:]):
            n = 8 if b - a > 2.0 else 4  # Suno が 8 拍で返した小節
            beats += [a + (b - a) * k / n for k in range(n)]
        self.beats, self.down = beats, down
        with open(song.work("drums.json")) as f:
            d = json.load(f)
        self.kick, self.perc = d["kick"], d["snare"]
        self.rms = np.load(song.work("rms.npy"))

        self.bg = self._background()
        self.f_title = ImageFont.truetype(FONT_BOLD, px(52))
        self.f_sec = ImageFont.truetype(FONT_LIGHT, px(26))
        self.f_next = ImageFont.truetype(FONT_LIGHT, px(40))
        self.f_tl = ImageFont.truetype(FONT_LIGHT, px(22))
        self.f_big = ImageFont.truetype(FONT_BOLD, px(110))
        self.f_credit = ImageFont.truetype(FONT_LIGHT, px(30))
        self.line_cache = {}
        rnd = random.Random(7)
        self.dots = [(rnd.uniform(0, self.W), rnd.uniform(0, self.H), rnd.uniform(0.3, 1.0),
                      px(rnd.uniform(1.5, 3.5))) for _ in range(140)]

    def px(self, v):
        """1920x1080 基準の値を、描画する解像度の値にする。"""
        return v * self.S

    def _background(self):
        """縦グラデーション + 薄いグリッド（静止）。"""
        W, H, px = self.W, self.H, self.px
        g = np.linspace(0, 1, H)[:, None]
        top, bot = np.array(self.pal.background_top), np.array(self.pal.outline)
        arr = (top * (1 - g) + bot * g)[:, None, :].repeat(W, axis=1)
        im = Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
        # 不透明な画像へ直接描くと alpha が合成されず線が不透明になるので、別レイヤーに描いて重ねる
        grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(grid)
        grid_color = self.pal.accent + (14,)
        for x in range(0, W, px(90)):
            d.line([(x, 0), (x, H)], fill=grid_color, width=self.S)
        for y in range(0, H, px(90)):
            d.line([(0, y), (W, y)], fill=grid_color, width=self.S)
        im.alpha_composite(grid)
        return im


C = None


def configure(song_dir, scale):
    global C
    C = Ctx(songmod.load(song_dir), scale)
    return C


# --------------------------------------------------------------------------- 時間 → 状態

def recent(times, t, window):
    """t 以前 window 秒以内の打点の経過時間（新しい順）。"""
    k = bisect.bisect_right(times, t)
    out = []
    while k > 0 and t - times[k - 1] < window:
        out.append(t - times[k - 1])
        k -= 1
    return out


def in_chorus(t):
    return any(a <= t < b for a, b in C.song.choruses)


def instrumental_alpha(t):
    """歌のない区間で曲名を出す濃さ（区間の両端 0.8 秒でフェード）。"""
    end = C.song.duration
    for a, b in C.song.instrumental:
        if a <= t < b:
            return min(1.0, (t - a) / 0.8 if a > 0 else 1.0, (b - t) / 0.8 if b < end else 1.0)
    return 0.0


def ease_out(u):
    return 1 - (1 - u) ** 3


# --------------------------------------------------------------------------- 描画

def line_images(i):
    """行 i の文字画像（未歌唱=白 / 歌唱済み=水色）とトークンごとの右端を作ってキャッシュする。"""
    if i in C.line_cache:
        return C.line_cache[i]
    px = C.px
    text = C.lines[i][0]
    size = px(84)
    while True:
        font = ImageFont.truetype(FONT_BOLD, size)
        l, t, r, b = font.getbbox(text, stroke_width=px(6))
        if r - l <= C.W - px(200) or size <= px(48):
            break
        size -= px(4)
    w, h = r - l + px(8), b - t + px(8)
    imgs = []
    for color in (C.pal.unsung, C.pal.sung):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((px(4) - l, px(4) - t), text, font=font, fill=color,
                                stroke_width=px(6), stroke_fill=C.pal.outline)
        imgs.append(im)
    edges, acc = [], ""
    for tok, _, _ in C.lines[i][3]:
        acc += tok
        edges.append(font.getlength(acc) + px(4))
    C.line_cache[i] = (imgs[0], imgs[1], edges)
    return C.line_cache[i]


def draw_dots(d, t):
    for x, y, depth, r in C.dots:
        yy = (y - t * C.px(12) * depth) % C.H
        a = int(40 + 50 * depth)
        d.ellipse([x - r, yy - r, x + r, yy + r], fill=C.pal.accent + (a,))


def draw_wave(d, t, chorus):
    """波形。振れ幅はドラム以外の RMS、形はゆっくり変わる正弦の和（本物の波形ではない）。"""
    px = C.px
    fi = min(int(t * FPS), len(C.rms) - 1)
    amp = px(25) + px(130) * float(C.rms[fi]) * (1.15 if chorus else 1.0)
    pts = []
    for x in range(px(80), C.W - px(80) + 1, px(8)):
        u = (x - px(80)) / (C.W - px(160))
        env = math.sin(math.pi * u) ** 1.5
        v = (math.sin(u * 12 + t * 2.1) * 0.6 + math.sin(u * 31 - t * 3.3) * 0.3
             + math.sin(u * 55 + t * 1.3) * 0.1)
        pts.append((x, C.WAVE_Y + v * amp * env))
    d.line(pts, fill=C.pal.accent + (60,), width=px(10), joint="curve")
    d.line(pts, fill=C.pal.accent + (230,), width=px(3), joint="curve")


def draw_kicks(d, t, chorus):
    """キック: 中心から広がる細いリング（0.5 秒で消える）。"""
    for dt in recent(C.kick, t, 0.5):
        u = dt / 0.5
        r = C.px(70) + C.RING_REACH * ease_out(u)
        a = int((150 if chorus else 110) * (1 - u) ** 2)
        cx, cy = C.CENTER
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=C.pal.accent + (a,), width=C.px(3))


def draw_percs(d, t):
    """スネア／打楽器: 中心から左右へ開く縦線（0.35 秒で消える）。"""
    for dt in recent(C.perc, t, 0.35):
        u = dt / 0.35
        off = C.px(40) + C.SNARE_SPREAD * ease_out(u)
        a = int(200 * (1 - u) ** 2)
        hh = C.SNARE_HALF * (1 - 0.4 * u)
        for sx in (-1, 1):
            x = C.W // 2 + sx * off
            d.line([(x, C.WAVE_Y - hh), (x, C.WAVE_Y + hh)], fill=(255, 255, 255, a), width=C.px(4))


def draw_timeline(d, t):
    """検出した打点を 2 段で流す。再生位置の縦線を通過した打点だけ塗る。"""
    px = C.px
    px_per_s = (C.TL_X1 - C.PLAYHEAD_X) / TL_SPAN
    f = C.f_tl
    rows = [("KICK", C.kick, C.TL_Y, C.pal.accent), (C.song.perc_label, C.perc, C.TL_Y + px(44), WHITE)]

    for b in C.down:  # 小節頭の目盛り
        if abs(b - t) <= TL_SPAN:
            x = C.PLAYHEAD_X + (b - t) * px_per_s
            d.line([(x, C.TL_Y - px(22)), (x, C.TL_Y + px(66))], fill=C.pal.accent + (45,), width=px(2))
    for label, hits, y, color in rows:
        d.text((C.TL_X0 - px(24) - f.getlength(label), y - px(12)), label, font=f, fill=(255, 255, 255, 150))
        d.line([(C.TL_X0, y), (C.TL_X1, y)], fill=(255, 255, 255, 35), width=px(1))
        lo = bisect.bisect_left(hits, t - TL_SPAN)
        hi = bisect.bisect_right(hits, t + TL_SPAN)
        for h in hits[lo:hi]:
            x = C.PLAYHEAD_X + (h - t) * px_per_s
            edge = min(1.0, (min(x - C.TL_X0, C.TL_X1 - x)) / px(80))  # 両端で薄くする
            if h <= t:
                r = px(9) if t - h < 0.12 else px(7)
                d.rectangle([x - r, y - r, x + r, y + r], fill=color + (int(255 * edge),))
            else:
                r = px(7)
                d.rectangle([x - r, y - r, x + r, y + r], fill=color + (int(60 * edge),),
                            outline=color + (int(190 * edge),), width=px(2))
    d.line([(C.PLAYHEAD_X, C.TL_Y - px(30)), (C.PLAYHEAD_X, C.TL_Y + px(74))], fill=C.pal.accent + (220,), width=px(3))


def draw_header(d, t):
    px, W = C.px, C.W
    title = C.song.title
    f = C.f_title
    d.text(((W - f.getlength(title)) / 2, px(60)), title, font=f, fill=C.pal.accent + (255,),
           stroke_width=px(3), stroke_fill=C.pal.outline)
    sec = [name for start, name in C.song.sections if t >= start][-1]
    label = "  ".join(sec)  # 字間を空ける
    fs = C.f_sec
    d.text(((W - fs.getlength(label)) / 2, px(140)), label, font=fs, fill=(255, 255, 255, 170))

    # 拍インジケーター。beats は小節頭から始まり、8 拍小節も 4 の倍数なので k % 4 が小節内の拍
    k = bisect.bisect_right(C.beats, t) - 1
    for j in range(4):
        x = W / 2 + px(32) * (j - 1.5)
        c = C.pal.accent + (255,) if k >= 0 and j == k % 4 else (255, 255, 255, 60)
        d.rectangle([x - px(8), px(200 - 8), x + px(8), px(200 + 8)], fill=c)


def draw_instrumental(base, t):
    """歌のない区間: 歌詞の位置に曲名。最後の区間には credit を添える。"""
    a = instrumental_alpha(t)
    if a <= 0:
        return
    px, W = C.px, C.W
    layer = Image.new("RGBA", (W, px(260)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = C.f_big
    title = C.song.title
    d.text(((W - f.getlength(title)) / 2, px(40)), title, font=f, fill=C.pal.accent + (255,),
           stroke_width=px(6), stroke_fill=C.pal.outline)
    if t >= C.song.instrumental[-1][0] and C.song.credit:
        fc = C.f_credit
        credit = C.song.credit
        d.text(((W - fc.getlength(credit)) / 2, px(190)), credit, font=fc, fill=(255, 255, 255, 190))
    if a < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * a)))
    base.alpha_composite(layer, (0, C.LYRIC_Y - px(110)))


def draw_lyrics(base, t):
    i = lyr.current_line(C.lines, t, LINE_LINGER)
    if i is None:
        return
    px = C.px
    white, blue, edges = line_images(i)
    s = C.lines[i][1]
    appear = min(1.0, max(0.0, (t - (s - 0.25)) / 0.25))
    dy = int(px(30) * (1 - ease_out(appear)))
    x = (C.W - white.width) // 2
    y = C.LYRIC_Y - white.height // 2 + dy

    layer = Image.new("RGBA", white.size, (0, 0, 0, 0))
    layer.alpha_composite(white)
    p = lyr.progress(C.lines[i][3], t)
    k = int(p)
    if p > 0:
        prev = edges[k - 1] if k > 0 else px(4)
        nxt = edges[min(k, len(edges) - 1)]
        cut = int(prev + (nxt - prev) * (p - k))
        if cut > 0:
            layer.alpha_composite(blue.crop((0, 0, cut, blue.height)), (0, 0))
    if appear < 1:
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * appear)))
    base.alpha_composite(layer, (x, y))

    # 次の行を小さく薄く（間奏をまたぐ行は出さない）
    if i + 1 < len(C.lines) and C.lines[i + 1][1] - t < 4.0:
        nt = C.lines[i + 1][0]
        f = C.f_next
        ImageDraw.Draw(base).text(((C.W - f.getlength(nt)) / 2, C.LYRIC_Y + px(90)), nt, font=f,
                                  fill=(255, 255, 255, 90))


def draw_motion(base, t, chorus):
    """点・リング・縦線（ぼかした光と重ねる）。サムネイルの背景もこれを使う。"""
    fx = Image.new("RGBA", (C.W, C.H), (0, 0, 0, 0))
    d = ImageDraw.Draw(fx)
    draw_dots(d, t)
    draw_kicks(d, t, chorus)
    draw_percs(d, t)
    base.alpha_composite(fx.filter(ImageFilter.GaussianBlur(C.px(6))))
    base.alpha_composite(fx)


def render_image(t):
    chorus = in_chorus(t)
    base = C.bg.copy()
    draw_motion(base, t, chorus)

    top = Image.new("RGBA", (C.W, C.H), (0, 0, 0, 0))
    d2 = ImageDraw.Draw(top)
    draw_wave(d2, t, chorus)
    draw_header(d2, t)
    draw_timeline(d2, t)
    base.alpha_composite(top)
    draw_instrumental(base, t)
    draw_lyrics(base, t)

    # 頭と尻はネイビーへフェード（白には飛ばさない）
    fade = min(1.0, t / C.song.fade_in, (C.song.duration - t) / C.song.fade_out)
    if fade < 1:
        base = Image.blend(Image.new("RGBA", (C.W, C.H), C.pal.background_top + (255,)), base, max(0.0, fade))
    return base.convert("RGB")


def render_frame(n):
    return render_image(n / FPS).tobytes()


def _init_worker(song_dir, scale):
    configure(song_dir, scale)


def output_path(song, scale):
    H = 1080 * scale
    return song.work(f"mv_{song.slug}.mp4" if scale == 1 else f"mv_{song.slug}_{H}p.mp4")


def preview(song_dir, scale, times):
    configure(song_dir, scale)
    paths = []
    for s in times:
        p = C.song.work(f"preview_{s}.jpg")
        render_image(float(s)).save(p, quality=90)
        paths.append(p)
    return paths


def render_video(song_dir, scale):
    require_ffmpeg("ffmpeg")
    configure(song_dir, scale)
    song = C.song
    frames = round(song.duration * FPS)
    print(f"{frames} frames  {C.W}x{C.H}")
    silent = song.work(f"mv_video_{C.H}p.mp4")
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{C.W}x{C.H}", "-r", str(FPS), "-i", "-",
         # 平らな色面と輪郭が主役の映像なので animation に寄せ、YouTube の再エンコード前の画質に余裕を持たせる
         "-c:v", "libx264", "-preset", "slow", "-tune", "animation", "-crf", "16",
         "-pix_fmt", "yuv420p", silent],
        stdin=subprocess.PIPE,
    )
    with Pool(os.cpu_count(), initializer=_init_worker, initargs=(song_dir, scale)) as pool:
        for k, buf in enumerate(pool.imap(render_frame, range(frames), chunksize=8)):
            enc.stdin.write(buf)
            if k % 600 == 0:
                print(f"  {k}/{frames}")
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg (video) failed")

    out = output_path(song, scale)
    dur = song.duration
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", silent,
         "-t", f"{dur}", "-i", song.audio,
         "-map", "0:v", "-map", "1:a",
         "-af", f"afade=t=out:st={dur - song.fade_out}:d={song.fade_out}",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", out],
        check=True,
    )
    return out
