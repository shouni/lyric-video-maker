#!/usr/bin/env python3
"""Get karaoke timing from the audio and regenerate the ASS subtitles.

Usage:
    python3 align_subtitles.py <audio.mp3> <keyframes.zip|subtitles.ass|lyrics.txt> [output.ass]

Lyrics can be the subtitles.ass inside keyframes.zip, a standalone ASS file,
or plain text (one line = one subtitle line, blank lines ignored).
"""

import os
import re
import sys
import zipfile
import argparse
import stable_whisper
import pysubs2

PUNCT_PATTERN = re.compile(r'[　 、。！？!?,.\s\-\[\]\(\)「」『』〜♪…※☆★●○◎]')
TAG_PATTERN = re.compile(r'\{[^}]*\}')
LINE_BREAK_PATTERN = re.compile(r'\\[nNh]')
TAIL_MS = 300  # how long a line stays after its last character
FILL_GAP_MARGIN_MS = 100  # margin left before the next line when a line is extended over repeated singing
# first line only: keep the input ASS start when it is at most this much earlier than Whisper's (the sung entry)
LEAD_IN_TOLERANCE_MS = 3000
DEFAULT_K_CS = 10  # default length for characters left without timing


def words_to_chars(segments):
    """Spread Whisper's word timestamps evenly over their characters."""
    chars = []
    for seg in segments:
        if not (hasattr(seg, 'words') and seg.words):
            continue
        for w in seg.words:
            word = w.word.strip()
            if not word:
                continue
            n = len(word)
            per = (w.end - w.start) / n
            for i, ch in enumerate(word):
                chars.append({
                    "char": ch,
                    "start": w.start + i * per,
                    "end":   w.start + (i + 1) * per,
                })
    return chars


def ms(sec):
    """Convert seconds to integer milliseconds for ASS events."""
    return int(sec * 1000)


def subs_from_txt(path):
    """Build an ASS from plain-text lyrics (one line = one subtitle line).

    It uses the same Karaoke style as the existing subtitles.ass
    (Arial 64px, yellow highlight, PlayRes 1920x1080), so burn_subs.py can read it as is.
    Event times are placeholders (alignment overwrites them).
    """
    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    if not lines:
        raise SystemExit(f"Error: the lyrics text is empty: {path}")

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "1920"
    subs.info["PlayResY"] = "1080"
    subs.styles["Karaoke"] = pysubs2.SSAStyle(
        fontname="Arial",
        fontsize=64,
        primarycolor=pysubs2.Color(255, 255, 0),    # &H0000FFFF yellow
        secondarycolor=pysubs2.Color(255, 255, 255),  # &H00FFFFFF white
        outlinecolor=pysubs2.Color(0, 0, 0),
        backcolor=pysubs2.Color(0, 0, 0, 128),
        bold=True,
        outline=3,
        shadow=1,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginl=10,
        marginr=10,
        marginv=80,
    )
    for i, line in enumerate(lines):
        subs.append(pysubs2.SSAEvent(
            start=i * 5000,
            end=(i + 1) * 5000,
            style="Karaoke",
            text=line,
        ))
    return subs


def plain_text(text):
    """Remove override and line-break tags from an ASS event's text."""
    return LINE_BREAK_PATTERN.sub('', TAG_PATTERN.sub('', text)).strip()


def load_source_subs(path):
    """Load the lyrics input (keyframes.zip / ASS / plain text) and return an SSAFile."""
    if not os.path.exists(path):
        raise SystemExit(f"Error: lyrics input not found: {path}")
    if path.endswith(".txt"):
        return subs_from_txt(path)
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "subtitles.ass" not in names:
                raise SystemExit(
                    f"Error: keyframes.zip has no subtitles.ass. Files in the ZIP: {names}"
                )
            with zf.open("subtitles.ass") as f:
                return pysubs2.SSAFile.from_string(f.read().decode("utf-8"))
    return pysubs2.load(path)


def extract_lyric_lines(subs):
    """Take the lyric lines of events that have text, in order."""
    return [plain for plain in (plain_text(e.text) for e in subs) if plain]


def map_chars_to_lines(lyric_lines, whisper_chars):
    """Assign the character timings Whisper detected to each lyric line.

    If the character counts without punctuation and symbols (PUNCT_PATTERN) differ, every line's timing would
    shift, so it raises ValueError and stops (this catches lyrics that differ from what was sung).
    """
    flat_orig = [
        {"line": line_idx, "char": ch}
        for line_idx, line in enumerate(lyric_lines)
        for ch in line
        if not PUNCT_PATTERN.match(ch)
    ]
    flat_whisper = [c for c in whisper_chars if PUNCT_PATTERN.match(c["char"]) is None]

    if len(flat_orig) != len(flat_whisper):
        raise ValueError(
            f"character counts differ (orig={len(flat_orig)}, whisper={len(flat_whisper)}). "
            "Stopping the alignment, because every timing after this would shift."
        )

    line_char_map = {}
    for orig, timing in zip(flat_orig, flat_whisper):
        line_char_map.setdefault(orig["line"], []).append(timing)
    return line_char_map


def build_karaoke_text(plain, char_timings):
    """Build ASS text with \\k tags from one lyric line and its character timings.

    Punctuation is folded into the \\k of the character before (punctuation itself is not sung).
    """
    k_parts = []
    ti = 0
    for ch in plain:
        if PUNCT_PATTERN.match(ch):
            if k_parts:
                k_parts[-1]["text"] += ch
            else:
                k_parts.append({"k_cs": 0, "text": ch})
        elif ti < len(char_timings):
            t = char_timings[ti]
            # \k advances continuously, so each length runs to the next character's start and the pause
            # between characters (commas, breaths) goes to the character before. Using only the voiced span
            # would let the highlight run ahead of the audio by those pauses.
            # Round on the start times so rounding errors do not build up within a line.
            nxt = char_timings[ti + 1]["start"] if ti + 1 < len(char_timings) else t["end"]
            k_parts.append({"k_cs": max(1, round(nxt * 100) - round(t["start"] * 100)), "text": ch})
            ti += 1
        else:
            k_parts.append({"k_cs": DEFAULT_K_CS, "text": ch})
    return "".join(f"{{\\k{p['k_cs']}}}{p['text']}" for p in k_parts)


def fill_repeat_gaps(subs):
    """Close the gap between lines up to just before the next line (keeps lyrics on screen during repeats)."""
    text_events = [e for e in subs if plain_text(e.text)]
    for curr, next_ev in zip(text_events, text_events[1:]):
        if next_ev.start - curr.end > FILL_GAP_MARGIN_MS:
            curr.end = next_ev.start - FILL_GAP_MARGIN_MS


def trim_overlaps(subs):
    """Cut a line's end at the next line's start where they overlap.

    Each line gets a TAIL_MS tail, so it overlaps when the next line starts right away. burn_subs.py draws
    only one line (the earlier one wins), so it is unaffected, but libass would stack two lines in the overlap,
    so it is resolved in the ASS itself.
    """
    text_events = [e for e in subs if plain_text(e.text)]
    for curr, next_ev in zip(text_events, text_events[1:]):
        # if the next line starts before this one, cutting would make the length 0 or less, so leave it
        if curr.start < next_ev.start < curr.end:
            curr.end = next_ev.start


def build_aligned_subs(subs_orig, line_char_map, verbose=True):
    """Generate a new ASS with \\k tags from the original ASS and the per-line character timings."""
    new_subs = pysubs2.SSAFile()
    new_subs.info = subs_orig.info.copy()
    new_subs.styles = subs_orig.styles.copy()

    valid_line_idx = 0
    for event in subs_orig:
        plain = plain_text(event.text)
        if not plain:
            # keep events without text (empty or tags only) as they are
            new_subs.append(event.copy())
            continue

        char_timings = line_char_map.get(valid_line_idx)
        valid_line_idx += 1
        if not char_timings:
            # A punctuation-only line has no characters to match, so no timing is assigned.
            # The input ASS times (placeholders for txt input) stay, unrelated to the audio.
            print(
                f"Warning: cannot assign timing to line {valid_line_idx} "
                f"(punctuation only?): {plain} → keeping the input ASS times",
                file=sys.stderr,
            )
            new_subs.append(event.copy())
            continue

        line_start_s = char_timings[0]["start"]
        line_end_s = char_timings[-1]["end"] + TAIL_MS / 1000

        new_event = event.copy()
        # first line only: if the input ASS is slightly earlier than Whisper, keep it (the sung entry)
        if valid_line_idx == 1 and 0 < ms(line_start_s) - event.start <= LEAD_IN_TOLERANCE_MS:
            new_event.start = event.start
        else:
            new_event.start = ms(line_start_s)
        new_event.end = ms(line_end_s)
        new_event.text = build_karaoke_text(plain, char_timings)
        new_subs.append(new_event)

        if verbose:
            print(f"  line {valid_line_idx}: {line_start_s:.2f}s - {line_end_s:.2f}s | {plain}")

    fill_repeat_gaps(new_subs)
    trim_overlaps(new_subs)
    return new_subs


def main():
    """Align the lyric text to the audio and write an ASS with \\k tags."""
    parser = argparse.ArgumentParser(description="Get karaoke timing from the audio and regenerate the ASS subtitles.")
    parser.add_argument("audio", help="audio to align (mp3 / wav etc.; a separated vocal is more accurate)")
    parser.add_argument("subtitles_in", help="lyrics input (keyframes.zip / subtitles.ass / lyrics.txt)")
    parser.add_argument("subtitles_out", nargs="?", default="subtitles_aligned.ass", help="output ASS (default: subtitles_aligned.ass)")
    parser.add_argument("--model", default="large-v3", help="Whisper model size (base / small / medium / large-v3, default: large-v3)")
    parser.add_argument("--language", default="ja", help="language code of the lyrics for Whisper (default: ja)")
    args = parser.parse_args()

    audio = args.audio
    ass_out = args.subtitles_out

    if not os.path.exists(audio):
        raise SystemExit(f"Error: audio file not found: {audio}")

    subs_orig = load_source_subs(args.subtitles_in)
    lyric_lines = extract_lyric_lines(subs_orig)
    if not lyric_lines:
        raise SystemExit(f"Error: no lyric lines: {args.subtitles_in}")

    print("Loading the Whisper model...")
    model = stable_whisper.load_model(args.model)

    print("Aligning...")
    result = model.align(audio, "\n".join(lyric_lines), language=args.language)

    # result.adjust_by_silence(audio, vad=True) corrects timing using silence, but a music track with backing
    # has no silence to find, so it does more harm than good and is not used.

    all_chars = words_to_chars(result.segments)
    print(f"characters detected: {len(all_chars)}")

    try:
        line_char_map = map_chars_to_lines(lyric_lines, all_chars)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"characters matched: {sum(len(v) for v in line_char_map.values())}")

    new_subs = build_aligned_subs(subs_orig, line_char_map)
    new_subs.save(ass_out)
    print(f"\ndone: {ass_out}")


if __name__ == "__main__":
    main()
