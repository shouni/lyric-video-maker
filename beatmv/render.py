"""Draw motion graphics driven by beats and lyrics, frame by frame with Pillow, and mux them with the song in FFmpeg.

Coordinates, widths and text sizes are written in design px for a Layout (LANDSCAPE 1920x1080, PORTRAIT
1080x1920 for Shorts) and drawn at `scale` (landscape default 2 = 3840x2160).
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
import functools
import json
import math
import os
import random
import re
import subprocess
from dataclasses import dataclass
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from beatmv import lyrics as lyr
from beatmv import song as songmod
from beatmv import style as stylemod
from burn_subs import best_split_index, font_candidates, require_ffmpeg

FPS = 30

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


@functools.cache
def jp_font_path(weight):
    """Font file for Japanese text in this weight: Hiragino, else burn_subs' candidates (one weight for all)."""
    for path in (_JP_WEIGHT[weight], *font_candidates()):
        if os.path.exists(path):
            return path
    raise RuntimeError("no font that can draw Japanese was found; set BURN_SUBS_FONT to a font file")


def font_for(text, weight, size):
    """Typeface for `text`: Hiragino for lines containing Japanese, the EN_FONT Latin typeface for Latin-only lines."""
    size = int(round(size))
    if EN_FONT not in _EN or lyr.has_japanese(text) or not os.path.exists(_EN[EN_FONT][0]):
        return ImageFont.truetype(jp_font_path(weight), size)
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
# The translation is drawn semibold (W6): W3 loses its thin strokes once outlined
# ghost-note star radius. 8 was invisible; 14 stood out more than a strong snare's square (radius 7–9)
GHOST_STAR_R = 11
LYRIC_FLOOR = 48   # smallest lyric size; a line still too wide at this size wraps into two rows
ROW_GAP = 10       # gap between the two rows of a wrapped lyric or translation
# A clip (render --from / --to) fades over these instead of the song's fade_in / fade_out, unless it starts or
# ends where the song does. Values from the Overclock Short: the entry is sung at the head of the clip, so
# the fade-in only hides the cut.
CLIP_FADE_IN = 0.08
CLIP_FADE_OUT = 1.4


@dataclass(frozen=True)
class Layout:
    """Where things go for one aspect ratio, in design px (multiplied by the scale through Ctx.px())."""
    name: str
    w: int
    h: int
    title_y: int
    title_font: int
    sec_y: int
    sec_font: int            # section name size in the header
    beat_y: int
    beat_step: int           # distance between beat squares
    beat_half: int           # half the side of a beat square
    wave_y: int
    wave_margin: int         # gap between the frame edge and each end of the waveform
    wave_step: int
    wave_freqs: tuple        # spatial frequencies of the three sines
    wave_base: int           # amplitude at silence
    wave_gain: int           # extra amplitude at full loudness
    ring_reach: int          # how far the kick ring widens
    snare_half: int          # half the length of the percussion lines
    snare_spread: int        # how far the percussion lines open
    bass_y: int
    bass_reach: int          # extra half-length of the bass band at full loudness
    lyric_y: int
    lyric_margin: int        # total width kept free beside the lyric (both sides together)
    next_dy: int             # next-line preview, below lyric_y
    next_font: int
    tl_y: int                # y of the first timeline row (KICK); the second is 44 * tl_scale px below
    tl_x0: int
    tl_x1: int
    tl_span: float           # seconds shown on each side of the playhead in the timeline
    tl_scale: float          # timeline scale (hits, labels, row spacing)
    dots: int


# 16:9. sec_font 26 was unreadable on a phone; at tl_scale 1.0, KICK / SNARE were unreadable at phone size.
LANDSCAPE = Layout(
    name="landscape", w=1920, h=1080,
    title_y=60, title_font=52, sec_y=140, sec_font=36, beat_y=200, beat_step=32, beat_half=8,
    wave_y=420, wave_margin=80, wave_step=8, wave_freqs=(12, 31, 55), wave_base=25, wave_gain=130,
    ring_reach=280, snare_half=150, snare_spread=780, bass_y=605, bass_reach=520,
    lyric_y=740, lyric_margin=200, next_dy=90, next_font=40,
    tl_y=935, tl_x0=420, tl_x1=1500, tl_span=2.5, tl_scale=1.3, dots=140,
)
# 9:16 (Shorts). The values are those of the per-song Shorts scripts this replaces (Overclock's
# build_short.py): the header starts below the Shorts top bar and the timeline ends above the bottom UI.
# A phone shows 1080 px across the full screen width, so text can be smaller in design px than in landscape.
PORTRAIT = Layout(
    name="portrait", w=1080, h=1920,
    title_y=230, title_font=64, sec_y=330, sec_font=30, beat_y=400, beat_step=34, beat_half=9,
    wave_y=820, wave_margin=40, wave_step=6, wave_freqs=(9, 23, 41), wave_base=30, wave_gain=150,
    ring_reach=380, snare_half=180, snare_spread=470, bass_y=1050, bass_reach=400,
    lyric_y=1290, lyric_margin=80, next_dy=100, next_font=44,
    tl_y=1560, tl_x0=200, tl_x1=1020, tl_span=2.0, tl_scale=1.1, dots=90,
)


class Ctx:
    """Settings for one song at one resolution and layout, and assets built once per worker."""

    def __init__(self, song, scale, portrait=False, clip=None):
        self.song = song
        self.pal = stylemod.load(song.style)
        self.S = scale
        self.L = L = PORTRAIT if portrait else LANDSCAPE
        px = self.px
        self.W, self.H = px(L.w), px(L.h)
        self.LYRIC_Y = px(L.lyric_y)
        self.WAVE_Y = px(L.wave_y)
        self.CENTER = (self.W // 2, self.WAVE_Y)
        self.RING_REACH = px(L.ring_reach)
        self.SNARE_HALF = px(L.snare_half)
        self.SNARE_SPREAD = px(L.snare_spread)
        self.TL_Y = px(L.tl_y)
        self.TL_X0, self.TL_X1 = px(L.tl_x0), px(L.tl_x1)
        self.PLAYHEAD_X = (self.TL_X0 + self.TL_X1) // 2

        # the span drawn (the whole song, or a clip) and the fades at its two ends
        self.T0, self.T1 = clip or (0.0, song.duration)
        self.fade_in = song.fade_in if self.T0 <= 0 else CLIP_FADE_IN
        self.fade_out = song.fade_out if self.T1 >= song.duration else CLIP_FADE_OUT

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
        self.BASS_Y = px(L.bass_y)

        self.bg = self._background()
        # titles shrink only when they would not fit the frame (a long title in portrait)
        self.f_title = fit_font(song.title, "bold", px(L.title_font), self.W - px(120))
        self.f_sec = font_for("CHORUS", "light", px(L.sec_font))  # section names are Latin
        self.f_tl = font_for("KICK", "light", px(22 * L.tl_scale))
        self.f_big = fit_font(song.title, "bold", px(110), self.W - px(120))
        self.f_credit = font_for(song.credit, "light", px(30))
        self.line_cache = {}
        self.trans_cache = {}
        self.next_cache = {}
        rnd = random.Random(7)
        self.dots = [(rnd.uniform(0, self.W), rnd.uniform(0, self.H), rnd.uniform(0.3, 1.0),
                      px(rnd.uniform(1.5, 3.5))) for _ in range(L.dots)]

    def px(self, v):
        """Convert a design-px value (see Layout) to the render resolution."""
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


def configure(song_dir, scale, portrait=False, clip=None):
    global C
    C = Ctx(songmod.load(song_dir), scale, portrait, clip)
    return C


def fit_font(text, weight, size, max_w, floor=12, step=2, font_at=None):
    """A font at `size`, made smaller in `step`s while `text` is wider than max_w and the size is above `floor`.

    font_at(size) builds the font; the default is font_for(text, weight, size).
    """
    font_at = font_at or (lambda sz: font_for(text, weight, sz))
    font = font_at(size)
    while font.getlength(text) > max_w and size > floor:
        size -= step
        font = font_at(size)
    return font


def faded(im, alpha):
    """A copy of an RGBA image with its opacity scaled by alpha (the image itself when alpha is 1)."""
    if alpha >= 1:
        return im
    out = im.copy()
    out.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
    return out


def split_rows(units, font):
    """Split text units into two rows at the best-balanced boundary (burn_subs' rule: after a unit that ends
    in a space when there is one, so English breaks between words). One row if it cannot be split."""
    idx = best_split_index(ImageDraw.Draw(Image.new("RGB", (1, 1))), units, font, 0)
    return [units] if idx is None else [units[:idx], units[idx:]]


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

def text_image(text, font, color, stroke):
    """One outlined row of text on a transparent image, and the x of the text origin inside it."""
    x0, t, r, b = font.getbbox(text, stroke_width=stroke)
    pad = C.px(4)
    im = Image.new("RGBA", (r - x0 + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((pad - x0, pad - t), text, font=font, fill=color,
                            stroke_width=stroke, stroke_fill=C.pal.outline)
    return im, pad - x0


def line_images(i):
    """Build and cache line i's rows: per row (unsung image, sung image, each token's right edge, first token
    index, end token index). Unsung = white, sung = light blue.

    Hook lines (`Override! Override!` etc., lyrics.is_hook_line) use larger text and fill with hook_color,
    so the shout that opens a chorus stands out by size and colour alone, without flashing or beat zoom.

    A line shrinks to fit the width down to LYRIC_FLOOR. If it is still too wide (long lines in portrait), it
    wraps into two rows at a token boundary and takes the largest size at which both rows fit.
    """
    if i in C.line_cache:
        return C.line_cache[i]
    px = C.px
    text = C.lines[i][0]
    tokens = [tok for tok, _, _ in C.lines[i][3]]
    hook = lyr.is_hook_line(text)
    stroke = px(6)
    max_w = C.W - px(C.L.lyric_margin)
    floor = px(LYRIC_FLOOR)

    def width(txt, font):
        x0, _, r, _ = font.getbbox(txt, stroke_width=stroke)
        return r - x0

    def fit(texts):
        size = px(HOOK_FONT if hook else LYRIC_FONT)
        while True:
            font = font_for(text, "bold", size)
            if all(width(txt, font) <= max_w for txt in texts) or size <= floor:
                return font
            size -= px(4)

    font = fit([text])
    rows = [tokens]
    if width(text, font) > max_w:
        rows = split_rows(tokens, font)
        if len(rows) == 2:
            font = fit(["".join(r).strip() for r in rows])

    out, start = [], 0
    for row in rows:
        # a wrapped row loses the space at the break; the edges are measured on the text as drawn
        lead = len("".join(row)) - len("".join(row).lstrip()) if len(rows) > 1 else 0
        row_text = "".join(row).strip() if len(rows) > 1 else text
        imgs = [text_image(row_text, font, color, stroke)[0]
                for color in (C.pal.unsung, C.pal.hook if hook else C.pal.sung)]
        edges, acc = [], ""
        for tok in row:
            acc += tok
            edges.append(font.getlength(acc[lead:].rstrip() if len(rows) > 1 else acc) + px(4))
        out.append((imgs[0], imgs[1], edges, start, start + len(row)))
        start += len(row)
    C.line_cache[i] = out
    return out


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
    L = C.L
    m = px(L.wave_margin)
    f1, f2, f3 = L.wave_freqs
    amp = px(L.wave_base) + px(L.wave_gain) * float(C.rms[fi]) * (1.15 if chorus else 1.0)
    pts = []
    for x in range(m, C.W - m + 1, px(L.wave_step)):
        u = (x - m) / (C.W - 2 * m)
        env = math.sin(math.pi * u) ** 1.5
        v = (math.sin(u * f1 + t * 2.1) * 0.6 + math.sin(u * f2 - t * 3.3) * 0.3
             + math.sin(u * f3 + t * 1.3) * 0.1)
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
    half = px(40) + px(C.L.bass_reach) * v
    cx, y = C.W // 2, C.BASS_Y
    d.line([(cx - half, y), (cx + half, y)], fill=C.pal.accent + (int(60 + 150 * v),), width=px(4))


def draw_ghost_star(d, x, y, passed, edge):
    """Ghost note: a small four-pointed star, in the palette's accent (light blue) so its shape and colour set it
    apart from a strong snare (white square). Faint outline before the playhead, filled after (no glow or flashing)."""
    px = C.px
    ro = px(GHOST_STAR_R * C.L.tl_scale)
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
    TL_SPAN = C.L.tl_span
    px_per_s = (C.TL_X1 - C.PLAYHEAD_X) / TL_SPAN
    f = C.f_tl
    k = C.L.tl_scale
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
    px, W, L = C.px, C.W, C.L
    title = C.song.title
    f = C.f_title
    d.text(((W - f.getlength(title)) / 2, px(L.title_y)), title, font=f, fill=C.pal.accent + (255,),
           stroke_width=px(3), stroke_fill=C.pal.outline)
    sec = [name for start, name in C.song.sections if t >= start][-1]
    label = "  ".join(sec)  # letter-spaced
    fs = C.f_sec
    d.text(((W - fs.getlength(label)) / 2, px(L.sec_y)), label, font=fs, fill=(255, 255, 255, 170))

    # beat indicator. Beats start on a downbeat and 8-beat bars are multiples of 4, so k % 4 is the beat in the bar
    k = bisect.bisect_right(C.beats, t) - 1
    for j in range(4):
        x = W / 2 + px(L.beat_step) * (j - 1.5)
        c = C.pal.accent + (255,) if k >= 0 and j == k % 4 else (255, 255, 255, 60)
        h = L.beat_half
        d.rectangle([x - px(h), px(L.beat_y - h), x + px(h), px(L.beat_y + h)], fill=c)


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
    base.alpha_composite(faded(layer, a), (0, C.LYRIC_Y - px(110)))


def draw_lyrics(base, t):
    i = lyr.current_line(C.lines, t, LINE_LINGER)
    if i is None:
        return
    px = C.px
    rows = line_images(i)
    s = C.lines[i][1]
    appear = min(1.0, max(0.0, (t - (s - 0.25)) / 0.25))
    dy = int(px(30) * (1 - ease_out(appear)))
    p = lyr.progress(C.lines[i][3], t)

    gap = px(ROW_GAP)
    block_h = sum(white.height for white, *_ in rows) + gap * (len(rows) - 1)
    y = C.LYRIC_Y - block_h // 2 + dy
    for white, blue, edges, first, end in rows:
        layer = Image.new("RGBA", white.size, (0, 0, 0, 0))
        layer.alpha_composite(white)
        q = min(p, end) - first  # tokens filled in this row (fractional); the rows fill one after the other
        if q > 0:
            k = int(q)
            prev = edges[k - 1] if k > 0 else px(4)
            nxt = edges[min(k, len(edges) - 1)]
            cut = int(prev + (nxt - prev) * (q - k))
            if cut > 0:
                layer.alpha_composite(blue.crop((0, 0, cut, blue.height)), (0, 0))
        base.alpha_composite(faded(layer, appear), ((C.W - white.width) // 2, y))
        y += white.height + gap
    bottom = y - gap

    if C.translation is not None:
        draw_translation(base, i, bottom, appear)
        return
    # the next line, small and faint (not across an interlude)
    if i + 1 < len(C.lines) and C.lines[i + 1][1] - t < 4.0:
        nt = C.lines[i + 1][0]
        if i + 1 not in C.next_cache:
            # Hiragino for every line, Latin ones too (as before); shrinks only when wider than the frame (portrait)
            C.next_cache[i + 1] = fit_font(
                nt, "light", px(C.L.next_font), C.W - px(C.L.lyric_margin), px(24),
                font_at=lambda sz: ImageFont.truetype(jp_font_path("light"), int(sz)))
        f = C.next_cache[i + 1]
        ny = C.LYRIC_Y + px(C.L.next_dy)
        if len(rows) > 1:  # a wrapped lyric reaches further down
            ny = max(ny, bottom + px(24))
        ImageDraw.Draw(base).text(((C.W - f.getlength(nt)) / 2, ny), nt, font=f, fill=(255, 255, 255, 90))


def translation_images(i):
    """Build and cache line i's translation as one or two row images (the text does not change per frame)."""
    if i in C.trans_cache:
        return C.trans_cache[i]
    text = C.translation[i]
    px = C.px
    max_w = C.W - px(C.L.lyric_margin)
    floor = px(TRANS_FONT * 0.7)
    # English translations run longer than Japanese lyrics, so shrink to fit the width (down to 70 % of TRANS_FONT)
    f = fit_font(text, "semibold", px(TRANS_FONT), max_w, floor, px(2))
    rows = [text]
    if f.getlength(text) > max_w:
        # still too wide (portrait): two rows, split between words, or between characters for Japanese
        units = re.findall(r"\S+\s*", text) if " " in text.strip() else list(text)
        parts = split_rows(units, f)
        if len(parts) == 2:
            rows = ["".join(part).strip() for part in parts]
            f = fit_font(max(rows, key=f.getlength), "semibold", px(TRANS_FONT), max_w, floor, px(2))
    C.trans_cache[i] = [text_image(row, f, C.pal.unsung, px(4))[0] for row in rows]
    return C.trans_cache[i]


def draw_translation(base, i, lyric_bottom, appear):
    """Show line i's translation under the lyric. It is not filled; it appears and disappears with the line
    (people read at different speeds).

    With a translation, the next-line preview is hidden: three rows in one place leave the viewer unsure which to read.
    """
    if not C.translation[i]:
        return
    y = int(lyric_bottom + C.px(TRANS_GAP))
    for im in translation_images(i):
        base.alpha_composite(faded(im, appear), ((C.W - im.width) // 2, y))
        y += im.height + C.px(ROW_GAP)


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
    fade = min(1.0, (t - C.T0) / C.fade_in, (C.T1 - t) / C.fade_out)
    if fade < 1:
        base = Image.blend(Image.new("RGBA", (C.W, C.H), C.pal.background_top + (255,)), base, max(0.0, fade))
    return base.convert("RGB")


def render_frame(n):
    return render_image(n / FPS).tobytes()


def _init_worker(song_dir, scale, portrait, clip):
    configure(song_dir, scale, portrait, clip)


def output_path(song, scale, portrait=False, clip=None):
    """mv_<slug>[_portrait][_<height>p][_<from>-<to>].mp4 in the work directory (no height at scale 1)."""
    name = f"mv_{song.slug}"
    if portrait:
        name += "_portrait"
    if scale != 1:
        name += f"_{(PORTRAIT.h if portrait else LANDSCAPE.h) * scale}p"
    if clip:
        name += f"_{clip[0]:g}-{clip[1]:g}"
    return song.work(name + ".mp4")


def clip_span(song, start, end):
    """Turn --from / --to into the span to draw: None for the whole song, else (start s, end s) inside it."""
    if start is None and end is None:
        return None
    t0 = 0.0 if start is None else float(start)
    t1 = song.duration if end is None else min(float(end), song.duration)
    if not 0 <= t0 < t1:
        raise ValueError(f"the clip must satisfy 0 <= from < to within the song's {song.duration} s: {t0}–{t1}")
    return (t0, t1)


def preview(song_dir, scale, times, portrait=False):
    configure(song_dir, scale, portrait)
    paths = []
    for s in times:
        p = C.song.work(f"preview_portrait_{s}.jpg" if portrait else f"preview_{s}.jpg")
        render_image(float(s)).save(p, quality=90)
        paths.append(p)
    return paths


def render_video(song_dir, scale, portrait=False, clip=None):
    """Render the song, or only the span `clip` = (start s, end s), and mux it with the audio."""
    require_ffmpeg("ffmpeg")
    configure(song_dir, scale, portrait, clip)
    song = C.song
    t0, t1 = C.T0, C.T1
    first = round(t0 * FPS)
    frames = round((t1 - t0) * FPS)
    print(f"{frames} frames  {C.W}x{C.H}")
    out = output_path(song, scale, portrait, clip)
    # the full landscape render keeps its old intermediate name; the others sit next to their output
    silent = out[:-4] + "_video.mp4" if portrait or clip else song.work(f"mv_video_{C.H}p.mp4")
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{C.W}x{C.H}", "-r", str(FPS), "-i", "-",
         # flat colours and outlines dominate, so tune for animation and leave headroom before YouTube re-encodes
         "-c:v", "libx264", "-preset", "slow", "-tune", "animation", "-crf", "16",
         "-pix_fmt", "yuv420p", silent],
        stdin=subprocess.PIPE,
    )
    with Pool(os.cpu_count(), initializer=_init_worker, initargs=(song_dir, scale, portrait, clip)) as pool:
        for k, buf in enumerate(pool.imap(render_frame, range(first, first + frames), chunksize=8)):
            enc.stdin.write(buf)
            if k % 600 == 0:
                print(f"  {k}/{frames}")
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg (video) failed")

    dur = t1 - t0
    afade = f"afade=t=out:st={dur - C.fade_out}:d={C.fade_out}"
    seek = []
    if clip:
        seek = ["-ss", f"{t0}"]
        if t0 > 0:  # a clip cut out of the middle also fades in, to hide the cut
            afade = f"afade=t=in:d={C.fade_in},{afade}"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", silent,
         *seek, "-t", f"{dur}", "-i", song.audio,
         "-map", "0:v", "-map", "1:a",
         "-af", afade,
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", out],
        check=True,
    )
    return out
