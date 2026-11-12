"""Read songs/<slug>/song.toml.

Only the title, audio, lyrics, beats, section times and drum rule differ per song; the drawing code is
shared. Write times taken from the finished audio, not from the recipe (Suno does not keep the recipe's
lengths).

Paths: files inside song_dir (lyrics / beats) are relative to song_dir; the audio and the work directory
are relative to the repository (~ and absolute paths also work). Audio and outputs stay out of the repo.
"""
import os
import tomllib
from dataclasses import dataclass

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

DRUM_RULES = ("rock", "edm", "trance", "dnb")
LYRICS_ALIGNERS = ("suno", "suno_kana", "whisper", "hybrid")
WAVE_SOURCES = ("mix", "vocals")


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
    sections: list[tuple[float, str]]
    choruses: list[tuple[float, float]]
    instrumental: list[tuple[float, float]]
    style: str = "karaoke_blue"  # styles/<name>.json (colours, shared with burn_subs)
    credit: str = "Made with Suno"
    fade_in: float = 0.5
    fade_out: float = 2.5
    thumbnail_window: tuple[float, float] = (0.0, 0.0)
    bass_band: bool = False  # show a bass loudness band under the waveform (needs the Demucs 4-stem split)
    wave: str = "mix"  # waveform source: mix = everything but drums, vocals = voice only (needs the 4-stem split)
    # translation (a text file in song_dir, one line per subtitle line). When set, it is shown under each line
    # and the next-line preview is hidden
    translation: str = ""
    # spans (start s, end s) confirmed by ear as snare rolls: kicks detected inside them are drawn on the
    # percussion row. Candidates come from `beatmv rolls` (or analyze); see drums.kick_runs
    snare_rolls: list[tuple[float, float]] = ()

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

    wave = d.get("wave", "mix")
    if wave not in WAVE_SOURCES:
        raise ValueError(f"wave must be one of {WAVE_SOURCES}: {wave!r}")

    style = d.get("style", "karaoke_blue")
    if not os.path.exists(os.path.join(REPO, "styles", f"{style}.json")):
        raise ValueError(f"style not found: styles/{style}.json")

    duration = float(d["duration"])
    snare_rolls = [(float(a), float(b)) for a, b in d.get("snare_rolls", [])]
    for a, b in snare_rolls:
        if not 0 <= a < b <= duration:
            raise ValueError(f"snare_rolls: each span must satisfy 0 <= start < end <= {duration}: [{a}, {b}]")
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
        perc_label=d.get("perc_label", "SNARE" if drum_rule in ("rock", "dnb") else "PERC"),
        sections=[(float(t), str(name)) for t, name in d["sections"]],
        choruses=[(float(a), float(b)) for a, b in d["choruses"]],
        instrumental=[(float(a), float(b)) for a, b in d["instrumental"]],
        style=style,
        credit=d.get("credit", "Made with Suno"),
        fade_in=float(d.get("fade_in", 0.5)),
        fade_out=float(d.get("fade_out", 2.5)),
        thumbnail_window=(float(thumb[0]), float(thumb[1])),
        bass_band=bool(d.get("bass_band", False)),
        wave=wave,
        translation=os.path.join(song_dir, d["translation"]) if d.get("translation") else "",
        snare_rolls=snare_rolls,
    )


def load_translation(song, n_lines):
    """Read one translation row per subtitle line. Stop if the counts differ (a one-row offset would give
    every line another line's translation).

    An empty row means "no translation for this line" (e.g. a repeated fragment such as `Only the pulse`).
    """
    if not song.translation:
        return None
    with open(song.translation, encoding="utf-8") as f:
        rows = f.read().split("\n")
    # Drop only the final newline. Trailing empty rows (lines with no translation, such as English ones) still count
    if rows and rows[-1] == "":
        rows.pop()
    rows = [r.strip() for r in rows]
    if len(rows) != n_lines:
        raise ValueError(f"{song.translation}: {len(rows)} translation rows for {n_lines} subtitle lines. "
                         "Give exactly one row per line")
    return rows
