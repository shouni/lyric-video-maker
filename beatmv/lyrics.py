"""Split ASS lyrics with \\k into per-character (token) timings. Rendering and SRT use the same timings.

The ASS comes from one of these (lyrics_aligner in song.toml):
- suno: built from Suno's lyric alignment (audio-lyrics-alignment). A line starts exactly where it is sung.
- whisper: lyrics aligned to the separated vocal with align_subtitles.py. For songs sung from kana, Suno's
  alignment also comes back in kana, so this is what gives kanji subtitles. align_subtitles.py pulls each
  line's start back to the end of the previous line and puts that time into the first character's \\k
  (e.g. \\k156). Filling it as is would colour the character before it is sung, so when the first
  character is longer than FIRST_SEG_MAX, filling starts FIRST_SEG_WIPE seconds before its end.
  On 駆け抜けろ Midnight that landed 0.16–0.29 s ahead of where Suno's alignment says the line is sung.
- suno_kana: built by `python -m beatmv align` from Suno's kana timings mapped onto the display (kanji) lyrics
  without Whisper (beatmv/suno_kana.py). Line starts are where they are sung, so no fix is applied.
- hybrid: built by `python -m beatmv align` with the line span from Suno and the timing inside the line
  from Whisper (beatmv/suno_align.py). The first character is already trimmed there, so no fix is applied.
"""
import bisect
import math
import re

import pysubs2

import burn_subs

FIRST_SEG_MAX = 0.5
FIRST_SEG_WIPE = 0.35


def load_lines(ass_path, aligner):
    """Return each line as (display text, line start, line end, [(token, start s, end s), ...])."""
    return load_lines_from(pysubs2.load(ass_path), aligner)


def load_lines_from(subs, aligner):
    """Same shape as load_lines, from an already loaded SSAFile."""
    out = []
    for e in subs:
        t0 = e.start / 1000
        segs = [(txt, t0 + a / 100, t0 + b / 100) for a, b, txt in burn_subs.parse_karaoke(e.text)]
        if not segs:
            continue
        if aligner == "whisper":
            txt, a, b = segs[0]
            if b - a > FIRST_SEG_MAX:
                segs[0] = (txt, b - FIRST_SEG_WIPE, b)
        out.append(("".join(x[0] for x in segs), t0, e.end / 1000, segs))
    return out


# Hook lines: English lines of up to four words with no Japanese (`Override! Override!`, `Shatter the silence`).
# The Suno prompts open each chorus with a shout of this shape, so it can be picked out of the lyrics.
HOOK_MAX_WORDS = 4
_JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff々〆ヶ]")
_LATIN = re.compile(r"[A-Za-z]")


def has_japanese(text):
    """Whether the text contains kana or kanji (used to tell Latin-only lines apart)."""
    return bool(_JAPANESE.search(text))


def is_hook_line(text):
    """A line with no Japanese, some Latin letters and at most HOOK_MAX_WORDS words is a hook line."""
    return (not _JAPANESE.search(text) and bool(_LATIN.search(text))
            and len(text.split()) <= HOOK_MAX_WORDS)


def current_line(lines, t, linger):
    """Index of the line on screen. It stays until just before the next line if that is close,
    otherwise until `linger` seconds after it is sung."""
    cur = None
    for i, (_, s, e, _) in enumerate(lines):
        nxt = lines[i + 1][1] if i + 1 < len(lines) else math.inf
        end = max(min(nxt - 0.25, e + linger), e + 0.4)
        if s - 0.25 <= t < end:
            cur = i
    return cur


def progress(segs, t):
    """Number of tokens filled so far (fractional). Each token fills over the length of its \\k."""
    k = bisect.bisect_right([a for _, a, _ in segs], t) - 1
    if k < 0:
        return 0.0
    _, a, b = segs[k]
    return k + min(1.0, (t - a) / max(b - a, 1e-3))


def to_srt(lines, out_path):
    """SRT for YouTube. Each line runs from its first sung character to its end, or to the next line's first
    sung character when that comes sooner (so two lines never overlap)."""
    subs = pysubs2.SSAFile()
    for i, (text, _, end, segs) in enumerate(lines):
        start = segs[0][1]
        if i + 1 < len(lines):
            end = min(end, lines[i + 1][3][0][1])
        subs.append(pysubs2.SSAEvent(start=round(start * 1000), end=round(end * 1000), text=text))
    subs.save(out_path, format_="srt")
    return subs
