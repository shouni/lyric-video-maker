"""beatmv の配色を styles/<name>.json から読む。burn_subs.py の --style-file と同じファイルを共有する。

同じファイルを使うのは、スライドショーの MV（burn_subs）と音から描く MV（beatmv）の配色を揃えるため。
beatmv が読むのは色だけで、font_size や outline の太さは使わない（beatmv は文字の大きさを自分の
レイアウトで決めていて、スタイル側の値を入れると配置が崩れる）。

| キー | burn_subs | beatmv |
| primary_color | 歌唱済みの色 | 同じ |
| secondary_color | 未歌唱の色 | 同じ |
| outline_color | 縁取り | 縁取りと背景グラデーションの下の色 |
| accent_color | （使わない） | リング・波形・点・グリッド・拍の四角・タイムライン・曲名。省略時は primary_color |
| background_top | （使わない） | 背景グラデーションの上の色 |

burn_subs.load_style_file は知らないキーを黙って通すので、beatmv だけのキーを足しても burn_subs は
変わらない。
"""
import json
import os
from dataclasses import dataclass

from beatmv.song import REPO
from burn_subs import HEX_COLOR_RE, hex_to_rgb

# 既定は karaoke_blue と同じ（これまでの MV の配色）
DEFAULTS = {
    "primary_color": "#8FE3FF",
    "secondary_color": "#FFFFFF",
    "outline_color": "#0B1A3A",
    "background_top": "#04091A",
}


@dataclass(frozen=True)
class Palette:
    sung: tuple
    unsung: tuple
    outline: tuple
    accent: tuple
    background_top: tuple


def _rgb(value, key, path):
    if not (isinstance(value, str) and HEX_COLOR_RE.fullmatch(value)):
        raise ValueError(f"{path}: {key} は '#RRGGBB' 形式で指定してください（{value!r}）")
    return hex_to_rgb(value)


def style_path(name):
    return os.path.join(REPO, "styles", f"{name}.json")


def load(name):
    """styles/<name>.json の色を読む。動画の配色が黙って変わらないよう、不正な色は警告でなくエラーにする。"""
    path = style_path(name)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError(f"{path}: JSON オブジェクトである必要があります")
    c = {**DEFAULTS, **{k: v for k, v in d.items() if k in DEFAULTS or k == "accent_color"}}
    c.setdefault("accent_color", c["primary_color"])
    return Palette(
        sung=_rgb(c["primary_color"], "primary_color", path),
        unsung=_rgb(c["secondary_color"], "secondary_color", path),
        outline=_rgb(c["outline_color"], "outline_color", path),
        accent=_rgb(c["accent_color"], "accent_color", path),
        background_top=_rgb(c["background_top"], "background_top", path),
    )
