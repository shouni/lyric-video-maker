"""YouTube thumbnail background (1280x720): one frame of the video with only the ring, vertical lines and waveform.

No lyrics, header or timeline. The title and artist name are not drawn here; the composing pipeline adds
them the same way it titles the cover art, so the thumbnail matches the cover.

It picks a moment inside song.toml's [thumbnail] window where both the kick ring and the percussion lines show.
"""
from PIL import Image, ImageDraw

from beatmv import render as r

SIZE = (1280, 720)


def pick_time(window):
    """Just after a percussion hit 0.10–0.22 s after a kick, inside the window (ring widening, lines open)."""
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
