"""Build lyrics.ass by combining Suno's lyric alignment with Whisper's (lyrics_aligner = "hybrid").

For songs sung from kana, neither one alone makes usable subtitles:
- Suno's alignment (audio-lyrics-alignment) places lines accurately, but its text comes back in kana,
  which cannot be shown as kanji subtitles.
- Whisper (align_subtitles.py) can time kanji character by character, but it pulls a line's start back
  to the end of the previous line and can leave seconds of empty time after a held word. On Overclock
  (2026-11-02) the last three lines were 2–4 s off.

So each line's start and end come from Suno, and the timing of characters inside the line from Whisper.
Whisper's line ends are not trusted: the first character is trimmed to FIRST_SEG_CAP and the last to
LAST_SEG_CAP, then the line is stretched linearly onto Suno's span.

Songs sung in English (lyrics_aligner = "suno") use Suno's alignment directly: words_to_ass writes one
\\k per word without Whisper.

Suno's alignment is only available through MCP, so the caller saves the JSON to
work/<slug>/suno_alignment.json (the audio-lyrics-alignment response as is, or the words array).
"""
import json
import re

import pysubs2

import burn_subs

# Whisper's first character carries the tail of the previous line, so it is trimmed to this before stretching.
FIRST_SEG_CAP = 0.4
# A held last character can run into the interlude, so it is trimmed to this.
LAST_SEG_CAP = 1.0
# Lines that still have a character longer than this after merging are listed for checking (two on Overclock).
HELD_WARN = 1.5
# Lines whose start differs by more than this are listed for checking by ear (2–4 s at the end of Overclock).
START_WARN = 2.0

# In English subtitles (words_to_ass), a gap between words longer than this becomes an empty \\k.
GAP_TOKEN = 0.15

_TAG = re.compile(r"\[[^\]]*\]")
# Punctuation of the previous word found at the start of a word (words_to_ass moves it back)
_LEADING_PUNCT = re.compile(r"[!?,.;:]+")


def load_words(path):
    """Take the words out of a saved Suno alignment (the response as is, or the words array)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    words = data.get("words") if isinstance(data, dict) else data
    if not isinstance(words, list) or not words:
        raise ValueError(f"{path}: no words found")
    return words


def line_spans(words):
    """Split Suno's words into (start s, end s) per sung line.

    Suno marks line breaks with a newline at the start of a word and mixes section tags (with directions,
    such as [Verse: ...]) into the words. If a word, with tags removed, has text after a newline, a new line
    starts there. If only whitespace follows the newline (e.g. [Outro] after the last word), the word still
    belongs to the previous line.
    """
    spans = []
    for w in words:
        text = _TAG.sub("", w["word"])
        start, end = float(w["start_s"]), float(w["end_s"])
        if "\n" in text and text.rsplit("\n", 1)[1].strip():
            spans.append([start, end])
        elif spans:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [tuple(s) for s in spans]


def _retime_line(segs, span):
    """Map one Whisper line (a list of [token, start, end]) onto Suno's span for that line."""
    segs = [list(s) for s in segs]
    if segs[0][2] - segs[0][1] > FIRST_SEG_CAP:
        segs[0][1] = segs[0][2] - FIRST_SEG_CAP
    if segs[-1][2] - segs[-1][1] > LAST_SEG_CAP:
        segs[-1][2] = segs[-1][1] + LAST_SEG_CAP
    ws, we = segs[0][1], segs[-1][2]
    ss, se = span
    scale = (se - ss) / max(we - ws, 1e-3)
    bounds = [ss + (s[1] - ws) * scale for s in segs] + [se]
    return [(s[0], bounds[i], bounds[i + 1]) for i, s in enumerate(segs)]


def merge(whisper_ass, words):
    """Return Whisper's ASS rebuilt on Suno's line spans, plus a list of lines worth checking.

    Stops if the line counts differ: one of them dropped or added a line, and guessing the mapping would
    fill lines with another line's timing.
    """
    subs = pysubs2.load(whisper_ass)
    spans = line_spans(words)
    if len(spans) != len(subs):
        raise ValueError(
            f"line counts differ: Suno {len(spans)} / Whisper {len(subs)}. "
            "One of them dropped a line, or the lyrics file differs from what was sung")

    warnings = []
    for n, (event, span) in enumerate(zip(subs, spans, strict=False), 1):
        t0 = event.start / 1000
        segs = [(txt, t0 + a / 100, t0 + b / 100) for a, b, txt in burn_subs.parse_karaoke(event.text)]
        if not segs:
            continue
        timed = _retime_line(segs, span)
        cs = [round(timed[0][1] * 100)] + [round(e * 100) for _, _, e in timed]
        event.start, event.end = cs[0] * 10, cs[-1] * 10
        event.text = "".join(f"{{\\k{cs[i + 1] - cs[i]}}}{tok}" for i, (tok, _, _) in enumerate(timed))

        held = max(timed, key=lambda s: s[2] - s[1])
        if held[2] - held[1] > HELD_WARN:
            warnings.append(f"line {n} \"{''.join(s[0] for s in timed)}\": \"{held[0]}\" lasts "
                            f"{held[2] - held[1]:.1f} s. Check how the hold is distributed")
        whisper_start = max(segs[0][1], segs[0][2] - FIRST_SEG_CAP)
        if abs(whisper_start - span[0]) > START_WARN:
            warnings.append(f"line {n}: Whisper and Suno start the line more than {START_WARN:.0f} s apart "
                            f"(Whisper {whisper_start:.1f} s / Suno {span[0]:.1f} s). "
                            "Using Suno's time")
    return subs, warnings


def words_to_ass(words):
    """Build an SSAFile with one \\k per word, per line, from Suno's words alone (lyrics_aligner = "suno").

    For songs sung in English, Suno's aligned words are the displayed lyrics, so Whisper is not used.
    Lines are split as in line_spans. A word spanning a newline (`s\\nColor`) is split: the part before
    joins the last word of the previous line, the part after starts the new line. Word times are kept as Suno gave them.
    """
    lines = []
    for w in words:
        text = _TAG.sub("", w["word"])
        start, end = float(w["start_s"]), float(w["end_s"])
        if "\n" in text and text.rsplit("\n", 1)[1].strip():
            head, tail = text.rsplit("\n", 1)
            head = head.replace("\n", "")
            if head.strip() and lines:
                tok, a, b = lines[-1][-1]
                lines[-1][-1] = (tok + head, a, b)
            lines.append([(tail, start, end)])
        else:
            text = text.replace("\n", "")
            if not lines:
                lines.append([])
            lines[-1].append((text, start, end))

    subs = pysubs2.SSAFile()
    for segs in lines:
        segs = [s for s in segs if s[0]]
        if not segs:
            continue
        # Suno attaches a word's trailing punctuation to the next word (`! Go`, `, cold`). Left there,
        # it fills together with the next word: the "!" after "ready" stayed white for
        # up to 10 s. Move it back onto the previous word
        for i in range(1, len(segs)):
            m = _LEADING_PUNCT.match(segs[i][0])
            if m:
                tok, a, b = segs[i - 1]
                segs[i - 1] = (tok + m.group(0), a, b)
                segs[i] = (segs[i][0][m.end():], segs[i][1], segs[i][2])
        segs = [s for s in segs if s[0]]
        segs[0] = (segs[0][0].lstrip(), segs[0][1], segs[0][2])
        segs[-1] = (segs[-1][0].rstrip(), segs[-1][1], segs[-1][2])
        # When the gap between words (end of one to start of the next) exceeds GAP_TOKEN, insert an empty
        # \\k. Otherwise the next word fills during the gap and is coloured before it is sung
        parts, t = [], segs[0][1]
        for tok, a, e in segs:
            if a - t > GAP_TOKEN:
                parts.append(("", t, a))
                t = a
            parts.append((tok, t, max(e, t)))
            t = max(e, t)
        cs = [round(parts[0][1] * 100)] + [round(e * 100) for _, _, e in parts]
        text = "".join(f"{{\\k{cs[i + 1] - cs[i]}}}{tok}" for i, (tok, _, _) in enumerate(parts))
        subs.append(pysubs2.SSAEvent(start=cs[0] * 10, end=cs[-1] * 10, text=text))
    return subs
