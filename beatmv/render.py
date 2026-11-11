"""Draw motion graphics driven by beats and lyrics, frame by frame with Pillow, and mux them with the song in FFmpeg.

Coordinates, widths and text sizes are written for 1920x1080 and drawn at `scale` (default 2 = 3840x2160).
YouTube serves uploads of 1440p and above as high-bitrate VP9/AV1, so text edges survive even at 1080p.

Constraints (from feedback on an earlier video): no full-screen flashes, no pulsing brightness, no beat zoom.
Beats show only as local shapes (kick ring, percussion lines, beat squares, hit timeline).

- In sections without singing (instrumental in song.toml) the title takes the lyric's place;
  the last one adds the credit.
- A lyric does not stay until the next line; it fades at most LINE_LINGER s after it is sung
  (no old line during interludes).

Every worker gets the same settings through configure() (macOS multiprocessing uses spawn, so module state
is not inherited).
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

# Typeface for lines without Japanese (English lyrics, hooks, English translations, titles, labels). Hiragino's
# Latin letters are wide and read as "Japanese-made Latin" to people abroad, so a Latin typeface is used instead.
# Helvetica Neue is the default (with SF Pro, English translations looked cramped against the Japanese line).
# BEATMV_EN_FONT=hiragino restores the previous look.
EN_FONT = os.environ.get("BEATMV_EN_FONT", "helvetica")
_EN = {
    # The third value is a size factor. A Latin typeface looks 15–20 % smaller than Hiragino's Latin letters at the
    # same point size, so it is scaled up to keep the line width (matched on the width of "Overclock the heart")
    "helvetica": ("/System/Library/Fonts/HelveticaNeue.ttc", {"bold": 1, "semibold": 10, "light": 7}, 1.18),
    "sf": ("/System/Library/Fonts/SFNS.ttf", {"bold": "Bold", "semibold": "Semibold", "light": "Regular"}, 1.25),
}
_JP_WEIGHT = {"bold": "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
              "semibold": "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
              "light": "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"}


def font_for(text, weight, size):
    """Typeface for `text`: Hiragino for lines containing Japanese, the EN_FONT Latin typeface for Latin-only lines."""
    size = int(round(size))
    if EN_FONT not in _EN or lyr.has_japanese(text):
        return ImageFont.truetype(_JP_WEIGHT[weight], size)
    path, faces, scale = _EN[EN_FONT]
    size = int(round(size * scale))
    face = faces[weight]
    if isinstance(face, int):
        return ImageFont.truetype(path, size, index=face)
    f = ImageFont.truetype(path, size)
    f.set_variation_by_name(face)
    return f

WHITE = (255, 255, 255)  # section name, labels, percussion lines, next line (white whatever the palette)

LINE_LINGER = 1.5  # longest a line stays after it is sung (s)
LYRIC_FONT = 84    # lyric text size (for 1920x1080; long lines shrink to 48)
HOOK_FONT = 104    # hook line text size: over 20 % larger than a normal line, shown as a shout
HOOK_FADE = 0.5    # seconds over which the waveform shifts to hook_color around a hook line
TRANS_FONT = 58    # translation size, 70 % of the lyric (84). 46 was called too small in the video (Only the Pulse)
TRANS_GAP = 30     # gap from the bottom of the lyric to the translation
FONT_TRANS = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"  # W3 loses its thin strokes once outlined
# ghost-note star radius. 8 was invisible; 14 stood out more than a strong snare's square (radius 7–9)
GHOST_STAR_R = 11
TL_SCALE = 1.3     # timeline scale (hits, labels, row spacing). At 1.0, KICK / SNARE were unreadable at phone size
SEC_FONT = 36      # section name size in the header. 26 was unreadable on a phone
TL_SPAN = 2.5      # seconds shown on each side of the playhead in the timeline


class Ctx:
    """Settings for one song at one resolution, and assets built once per worker."""

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
        self.TL_Y = px(935)  # y of the first timeline row (KICK); the second is 44 px below
        self.TL_X0, self.TL_X1 = px(420), px(1500)
        self.PLAYHEAD_X = (self.TL_X0 + self.TL_X1) // 2

        self.lines = lyr.load_lines(song.lyrics, song.lyrics_aligner)
        self.translation = songmod.load_translation(song, len(self.lines))
        # spans where a hook line is sung (first character's start to last character's end), for the waveform colour
        self.hook_spans = [(segs[0][1], segs[-1][2]) for text, _, _, segs in self.lines if lyr.is_hook_line(text)]
        with open(song.beats) as f:
            down = json.load(f)["downbeats"]
        beats = []
        for a, b in zip(down, down[1:], strict=False):
            n = 8 if b - a > 2.0 else 4  # a bar Suno reported as 8 beats
            beats += [a + (b - a) * k / n for k in range(n)]
        self.beats, self.down = beats, down
        with open(song.work("drums.json")) as f:
            d = json.load(f)
        self.kick, self.perc = d["kick"], d["snare"]
        self.ghost = d.get("ghost", [])  # dnb ghost notes and hats (only drawn small on the timeline)
        self.rms = np.load(song.work("vocal_rms.npy" if song.wave == "vocals" else "rms.npy"))
        self.bass_rms = np.load(song.work("bass_rms.npy")) if song.bass_band else None
        self.BASS_Y = px(605)

        self.bg = self._background()
        self.f_title = font_for(song.title, "bold", px(52))
        self.f_sec = font_for("CHORUS", "light", px(SEC_FONT))  # section names are Latin
        self.f_next = ImageFont.truetype(FONT_LIGHT, px(40))
        self.f_tl = font_for("KICK", "light", px(22 * TL_SCALE))
        self.f_big = font_for(song.title, "bold", px(110))
        self.f_credit = font_for(song.credit, "light", px(30))
        self.line_cache = {}
        rnd = random.Random(7)
        self.dots = [(rnd.uniform(0, self.W), rnd.uniform(0, self.H), rnd.uniform(0.3, 1.0),
                      px(rnd.uniform(1.5, 3.5))) for _ in range(140)]

    def px(self, v):
        """Convert a 1920x1080 value to the render resolution."""
        return v * self.S

    def _background(self):
        """Vertical gradient plus a faint grid (static)."""
        W, H, px = self.W, self.H, self.px
        g = np.linspace(0, 1, H)[:, None]
        top, bot = np.array(self.pal.background_top), np.array(self.pal.outline)
        arr = (top * (1 - g) + bot * g)[:, None, :].repeat(W, axis=1)
        im = Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
        # drawing straight onto an opaque image ignores alpha and makes lines opaque, so draw on a layer and composite
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


# --------------------------------------------------------------------------- time → state

def recent(times, t, window):
    """Time since each hit within `window` seconds before t (newest first)."""
    k = bisect.bisect_right(times, t)
    out = []
    while k > 0 and t - times[k - 1] < window:
        out.append(t - times[k - 1])
        k -= 1
    return out


def in_chorus(t):
    return any(a <= t < b for a, b in C.song.choruses)


def instrumental_alpha(t):
    """Opacity of the title in sections without singing (fades over 0.8 s at both ends)."""
    end = C.song.duration
    for a, b in C.song.instrumental:
        if a <= t < b:
            return min(1.0, (t - a) / 0.8 if a > 0 else 1.0, (b - t) / 0.8 if b < end else 1.0)
    return 0.0


def ease_out(u):
    return 1 - (1 - u) ** 3


# --------------------------------------------------------------------------- drawing

def line_images(i):
    """Build and cache line i's text images (unsung = white / sung = light blue) and each token's right edge.

    Hook lines (`Override! Override!` etc., lyrics.is_hook_line) use larger text and fill with hook_color,
    so the shout that opens a chorus stands out by size and colour alone, without flashing or beat zoom.
    """
    if i in C.line_cache:
        return C.line_cache[i]
    px = C.px
    text = C.lines[i][0]
    hook = lyr.is_hook_line(text)
    size = px(HOOK_FONT if hook else LYRIC_FONT)
    while True:
        font = font_for(text, "bold", size)
        x0, t, r, b = font.getbbox(text, stroke_width=px(6))
        if r - x0 <= C.W - px(200) or size <= px(48):
            break
        size -= px(4)
    w, h = r - x0 + px(8), b - t + px(8)
    imgs = []
    for color in (C.pal.unsung, C.pal.hook if hook else C.pal.sung):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((px(4) - x0, px(4) - t), text, font=font, fill=color,
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


def hook_weight(t):
    """Weight that is 1 while a hook line is sung and moves to 0 over HOOK_FADE s around it,
    so the colour shifts slowly instead of flashing."""
    w = 0.0
    for s, e in C.hook_spans:
        w = max(w, min(1.0, (t - (s - HOOK_FADE)) / HOOK_FADE, ((e + HOOK_FADE) - t) / HOOK_FADE))
    return max(0.0, w)


def mix_rgb(a, b, w):
    return tuple(int(round(x + (y - x) * w)) for x, y in zip(a, b, strict=False))


def draw_wave(d, t, chorus):
    """The waveform. Its amplitude follows the RMS chosen by song.wave (mix = all but drums, vocals = voice only);
    its shape is a slowly changing sum of sines (not the real waveform).
    It leans to hook_color while a hook line is sung."""
    px = C.px
    color = mix_rgb(C.pal.accent, C.pal.hook, hook_weight(t))
    fi = min(int(t * FPS), len(C.rms) - 1)
    amp = px(25) + px(130) * float(C.rms[fi]) * (1.15 if chorus else 1.0)
    pts = []
    for x in range(px(80), C.W - px(80) + 1, px(8)):
        u = (x - px(80)) / (C.W - px(160))
        env = math.sin(math.pi * u) ** 1.5
        v = (math.sin(u * 12 + t * 2.1) * 0.6 + math.sin(u * 31 - t * 3.3) * 0.3
             + math.sin(u * 55 + t * 1.3) * 0.1)
        pts.append((x, C.WAVE_Y + v * amp * env))
    d.line(pts, fill=color + (60,), width=px(10), joint="curve")
    d.line(pts, fill=color + (230,), width=px(3), joint="curve")


def draw_kicks(d, t, chorus):
    """Kick: a thin ring widening from the centre (gone after 0.5 s)."""
    for dt in recent(C.kick, t, 0.5):
        u = dt / 0.5
        r = C.px(70) + C.RING_REACH * ease_out(u)
        a = int((150 if chorus else 110) * (1 - u) ** 2)
        cx, cy = C.CENTER
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=C.pal.accent + (a,), width=C.px(3))


def draw_percs(d, t):
    """Snare / percussion: vertical lines opening left and right from the centre (gone after 0.35 s)."""
    for dt in recent(C.perc, t, 0.35):
        u = dt / 0.35
        off = C.px(40) + C.SNARE_SPREAD * ease_out(u)
        a = int(200 * (1 - u) ** 2)
        hh = C.SNARE_HALF * (1 - 0.4 * u)
        for sx in (-1, 1):
            x = C.W // 2 + sx * off
            d.line([(x, C.WAVE_Y - hh), (x, C.WAVE_Y + hh)], fill=(255, 255, 255, a), width=C.px(4))


def draw_bass(d, t):
    """Bass: a horizontal band under the waveform whose length and opacity follow the bass loudness (bass_band only)."""
    if C.bass_rms is None:
        return
    px = C.px
    v = float(C.bass_rms[min(int(t * FPS), len(C.bass_rms) - 1)])
    half = px(40) + px(520) * v
    cx, y = C.W // 2, C.BASS_Y
    d.line([(cx - half, y), (cx + half, y)], fill=C.pal.accent + (int(60 + 150 * v),), width=px(4))


def draw_ghost_star(d, x, y, passed, edge):
    """Ghost note: a small four-pointed star, in the palette's accent (light blue) so its shape and colour set it
    apart from a strong snare (white square). Faint outline before the playhead, filled after (no glow or flashing)."""
    px = C.px
    ro = px(GHOST_STAR_R * TL_SCALE)
    ri = ro * 0.32
    pts = []
    for k in range(8):
        a = math.pi / 4 * k - math.pi / 2
        r = ro if k % 2 == 0 else ri
        pts.append((x + r * math.cos(a), y + r * math.sin(a)))
    if passed:
        d.polygon(pts, fill=C.pal.accent + (int(220 * edge),))
    else:
        d.polygon(pts, outline=C.pal.accent + (int(120 * edge),), width=px(1))


def draw_timeline(d, t):
    """Scroll the detected hits in two rows. Only hits past the playhead line are filled."""
    px = C.px
    px_per_s = (C.TL_X1 - C.PLAYHEAD_X) / TL_SPAN
    f = C.f_tl
    k = TL_SCALE
    rows = [("KICK", C.kick, C.TL_Y, C.pal.accent), (C.song.perc_label, C.perc, C.TL_Y + px(44 * k), WHITE)]

    for b in C.down:  # downbeat ticks
        if abs(b - t) <= TL_SPAN:
            x = C.PLAYHEAD_X + (b - t) * px_per_s
            d.line([(x, C.TL_Y - px(22 * k)), (x, C.TL_Y + px(66 * k))], fill=C.pal.accent + (45,), width=px(2))
    for label, hits, y, color in rows:
        d.text((C.TL_X0 - px(24) - f.getlength(label), y - px(12 * k)), label, font=f, fill=(255, 255, 255, 150))
        d.line([(C.TL_X0, y), (C.TL_X1, y)], fill=(255, 255, 255, 35), width=px(1))
        if hits is C.perc:  # ghost notes as small stars on the same row (drawn first, under the strong snares)
            for h in C.ghost[bisect.bisect_left(C.ghost, t - TL_SPAN):bisect.bisect_right(C.ghost, t + TL_SPAN)]:
                x = C.PLAYHEAD_X + (h - t) * px_per_s
                edge = min(1.0, (min(x - C.TL_X0, C.TL_X1 - x)) / px(80))
                draw_ghost_star(d, x, y, h <= t, edge)
        lo = bisect.bisect_left(hits, t - TL_SPAN)
        hi = bisect.bisect_right(hits, t + TL_SPAN)
        for h in hits[lo:hi]:
            x = C.PLAYHEAD_X + (h - t) * px_per_s
            edge = min(1.0, (min(x - C.TL_X0, C.TL_X1 - x)) / px(80))  # fade at both ends
            if h <= t:
                r = px(9 * k) if t - h < 0.12 else px(7 * k)
                d.rectangle([x - r, y - r, x + r, y + r], fill=color + (int(255 * edge),))
            else:
                r = px(7 * k)
                d.rectangle([x - r, y - r, x + r, y + r], fill=color + (int(60 * edge),),
                            outline=color + (int(190 * edge),), width=px(2))
    d.line([(C.PLAYHEAD_X, C.TL_Y - px(30 * k)), (C.PLAYHEAD_X, C.TL_Y + px(74 * k))],
           fill=C.pal.accent + (220,), width=px(3))


def draw_header(d, t):
    px, W = C.px, C.W
    title = C.song.title
    f = C.f_title
    d.text(((W - f.getlength(title)) / 2, px(60)), title, font=f, fill=C.pal.accent + (255,),
           stroke_width=px(3), stroke_fill=C.pal.outline)
    sec = [name for start, name in C.song.sections if t >= start][-1]
    label = "  ".join(sec)  # letter-spaced
    fs = C.f_sec
    d.text(((W - fs.getlength(label)) / 2, px(140)), label, font=fs, fill=(255, 255, 255, 170))

    # beat indicator. Beats start on a downbeat and 8-beat bars are multiples of 4, so k % 4 is the beat in the bar
    k = bisect.bisect_right(C.beats, t) - 1
    for j in range(4):
        x = W / 2 + px(32) * (j - 1.5)
        c = C.pal.accent + (255,) if k >= 0 and j == k % 4 else (255, 255, 255, 60)
        d.rectangle([x - px(8), px(200 - 8), x + px(8), px(200 + 8)], fill=c)


def draw_instrumental(base, t):
    """Sections without singing: the title in the lyric's place; the last one adds the credit."""
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

    if C.translation is not None:
        draw_translation(base, i, y + white.height, appear)
        return
    # the next line, small and faint (not across an interlude)
    if i + 1 < len(C.lines) and C.lines[i + 1][1] - t < 4.0:
        nt = C.lines[i + 1][0]
        f = C.f_next
        ImageDraw.Draw(base).text(((C.W - f.getlength(nt)) / 2, C.LYRIC_Y + px(90)), nt, font=f,
                                  fill=(255, 255, 255, 90))


def draw_translation(base, i, lyric_bottom, appear):
    """Show line i's translation under the lyric. It is not filled; it appears and disappears with the line
    (people read at different speeds).

    With a translation, the next-line preview is hidden: three rows in one place leave the viewer unsure which to read.
    """
    text = C.translation[i]
    if not text:
        return
    px = C.px
    f = font_for(text, "semibold", px(TRANS_FONT))
    # English translations run longer than Japanese lyrics, so shrink to fit the width (down to 70 % of TRANS_FONT)
    size = px(TRANS_FONT)
    while f.getlength(text) > C.W - px(200) and size > px(TRANS_FONT * 0.7):
        size -= px(2)
        f = font_for(text, "semibold", size)
    x0, t, r, b = f.getbbox(text, stroke_width=px(4))
    im = Image.new("RGBA", (r - x0 + px(8), b - t + px(8)), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((px(4) - x0, px(4) - t), text, font=f, fill=C.pal.unsung,
                            stroke_width=px(4), stroke_fill=C.pal.outline)
    if appear < 1:
        im.putalpha(im.getchannel("A").point(lambda v: int(v * appear)))
    base.alpha_composite(im, ((C.W - im.width) // 2, int(lyric_bottom + px(TRANS_GAP))))


def draw_motion(base, t, chorus):
    """Dots, ring and lines (over a blurred glow). The thumbnail background uses this too."""
    fx = Image.new("RGBA", (C.W, C.H), (0, 0, 0, 0))
    d = ImageDraw.Draw(fx)
    draw_dots(d, t)
    draw_kicks(d, t, chorus)
    draw_percs(d, t)
    draw_bass(d, t)
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

    # fade the start and end to navy (never to white)
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
         # flat colours and outlines dominate, so tune for animation and leave headroom before YouTube re-encodes
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
