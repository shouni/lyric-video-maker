"""Read beatmv's colours from styles/<name>.json, the same files as burn_subs.py's --style-file.

Sharing the files keeps the slideshow videos (burn_subs) and the audio-drawn videos (beatmv) in one
palette. beatmv reads colours only, never font_size or outline width (beatmv sizes text with its own
layout, and the style's values would break it).

| key | burn_subs | beatmv |
| primary_color | sung colour | same |
| secondary_color | unsung colour | same |
| outline_color | outline | outline and the bottom of the background gradient |
| accent_color | (unused) | ring, waveform, bass band, dots, grid, beat squares, timeline, title. Defaults to
  primary_color |
| background_top | (unused) | top of the background gradient, and the colour the start and end fade to |
| hook_color | (unused) | sung colour of hook lines (lyrics.is_hook_line), and the waveform while one is sung.
  Default: cyan, as on covers |

burn_subs.load_style_file passes unknown keys through silently, so beatmv-only keys do not change burn_subs.
"""
import json
import os
from dataclasses import dataclass

from beatmv.song import REPO
from burn_subs import HEX_COLOR_RE, hex_to_rgb

# Defaults match karaoke_blue (the palette of the videos so far)
DEFAULTS = {
    "primary_color": "#8FE3FF",
    "secondary_color": "#FFFFFF",
    "outline_color": "#0B1A3A",
    "background_top": "#04091A",
    "hook_color": "#00E5FF",
}


@dataclass(frozen=True)
class Palette:
    sung: tuple
    unsung: tuple
    outline: tuple
    accent: tuple
    background_top: tuple
    hook: tuple


def _rgb(value, key, path):
    if not (isinstance(value, str) and HEX_COLOR_RE.fullmatch(value)):
        raise ValueError(f"{path}: {key} must be '#RRGGBB' ({value!r})")
    return hex_to_rgb(value)


def style_path(name):
    return os.path.join(REPO, "styles", f"{name}.json")


def load(name):
    """Read the colours in styles/<name>.json.

    Invalid colours are errors, not warnings, so a video's palette never changes silently."""
    path = style_path(name)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError(f"{path}: must be a JSON object")
    c = {**DEFAULTS, **{k: v for k, v in d.items() if k in DEFAULTS or k == "accent_color"}}
    c.setdefault("accent_color", c["primary_color"])
    return Palette(
        sung=_rgb(c["primary_color"], "primary_color", path),
        unsung=_rgb(c["secondary_color"], "secondary_color", path),
        outline=_rgb(c["outline_color"], "outline_color", path),
        accent=_rgb(c["accent_color"], "accent_color", path),
        background_top=_rgb(c["background_top"], "background_top", path),
        hook=_rgb(c["hook_color"], "hook_color", path),
    )
