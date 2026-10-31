"""songs/<slug>/song.toml を読む。

曲ごとに違うのは、曲名・音源・字幕・拍・セクションの時刻・ドラムの振り分け方だけで、描画のコードは
共有する。時刻はレシピではなく、できた音から取ったもの（Suno はレシピの秒数を守らない）を書く。

パスの解決: song_dir 内のファイル（lyrics / beats）は song_dir 基準、音源と作業ディレクトリは
リポジトリ基準（~ と絶対パスも可）。音源と生成物はリポジトリに入れない。
"""
import os
import tomllib
from dataclasses import dataclass
from typing import List, Tuple

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

DRUM_RULES = ("rock", "edm")
LYRICS_ALIGNERS = ("suno", "whisper")


@dataclass
class Song:
    slug: str
    title: str
    audio: str
    work_dir: str
    duration: float
    lyrics: str
    lyrics_aligner: str
    beats: str
    drum_rule: str
    perc_label: str
    sections: List[Tuple[float, str]]
    choruses: List[Tuple[float, float]]
    instrumental: List[Tuple[float, float]]
    style: str = "karaoke_blue"  # styles/<name>.json（配色。burn_subs と共有）
    credit: str = "Made with Suno"
    fade_in: float = 0.5
    fade_out: float = 2.5
    thumbnail_window: Tuple[float, float] = (0.0, 0.0)

    def work(self, *parts):
        return os.path.join(self.work_dir, *parts)


def _repo_path(p):
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(REPO, p)


def load(song_dir):
    song_dir = os.path.abspath(song_dir)
    with open(os.path.join(song_dir, "song.toml"), "rb") as f:
        d = tomllib.load(f)

    drum_rule = d["drum_rule"]
    if drum_rule not in DRUM_RULES:
        raise ValueError(f"drum_rule must be one of {DRUM_RULES}: {drum_rule!r}")
    aligner = d["lyrics_aligner"]
    if aligner not in LYRICS_ALIGNERS:
        raise ValueError(f"lyrics_aligner must be one of {LYRICS_ALIGNERS}: {aligner!r}")

    style = d.get("style", "karaoke_blue")
    if not os.path.exists(os.path.join(REPO, "styles", f"{style}.json")):
        raise ValueError(f"style not found: styles/{style}.json")

    duration = float(d["duration"])
    thumb = d.get("thumbnail", {}).get("window", [0.0, duration])
    return Song(
        slug=os.path.basename(song_dir),
        title=d["title"],
        audio=_repo_path(d["audio"]),
        work_dir=_repo_path(d["work_dir"]),
        duration=duration,
        lyrics=os.path.join(song_dir, d.get("lyrics", "lyrics.ass")),
        lyrics_aligner=aligner,
        beats=os.path.join(song_dir, d.get("beats", "beats.json")),
        drum_rule=drum_rule,
        perc_label=d.get("perc_label", "SNARE" if drum_rule == "rock" else "PERC"),
        sections=[(float(t), str(name)) for t, name in d["sections"]],
        choruses=[(float(a), float(b)) for a, b in d["choruses"]],
        instrumental=[(float(a), float(b)) for a, b in d["instrumental"]],
        style=style,
        credit=d.get("credit", "Made with Suno"),
        fade_in=float(d.get("fade_in", 0.5)),
        fade_out=float(d.get("fade_out", 2.5)),
        thumbnail_window=(float(thumb[0]), float(thumb[1])),
    )
