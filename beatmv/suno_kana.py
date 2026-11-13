"""Time Japanese lyrics character by character from Suno's alignment alone (lyrics_aligner = "suno_kana").

Songs sung from kana come back from Suno's alignment in kana, one or two characters per word, with good timing.
The subtitles show the lyrics as written (kanji). This maps one onto the other without Whisper:

1. Suno's words are split into characters. A word's span is shared evenly by its characters, and a small kana
   (ャュョァィゥェォヮ) joins the character before it, so the units are morae.
2. Each display line is aligned to its Suno line by dynamic programming. A kana in the display must equal the
   sung mora (hiragana and katakana compare equal), so the display's kana anchor the alignment. A run of other
   characters (kanji, digits) takes one or more morae: it is what was sung between the anchors. Among several
   alignments the one whose runs come closest to two morae per character wins.
3. Inside a run, each character starts at the mora nearest an even split of the run, but never at ン, ッ or ー,
   which cannot begin a reading (電車 デ|ン|シャ → 電 = デン, 車 = シャ). The exact split inside a word of
   several kanji (最終 さい/しゅう) is not known without a dictionary; it is an estimate.

No dictionary is used: the sung kana are the truth (a dictionary misreads names and 当て字), and a display line
whose kana do not match what Suno heard is listed for checking rather than guessed. On one song (2026-11-12)
this reproduced the hand-timed table of retime_from_suno.py, which hybrid had been 1 s off on many lines.
Characters wipe over their own sung span, with an empty \\k for gaps longer than GAP_TOKEN, as in English
(suno_align.words_to_ass).
"""
import re

import pysubs2

from beatmv.suno_align import GAP_TOKEN, _TAG

# A newline at the end of a word belongs to the previous line, which has no later word to time it by: each of
# those characters is held this long after the previous word (seen on the last kana of two words).
TAIL_HOLD = 0.5
# A character starting this long after the one before it in the same line is listed for checking by ear.
GAP_WARN = 1.0
# A run takes at most this many morae per character (no kanji reading is longer).
MAX_MORAE_PER_CHAR = 5

_SMALL = set("ァィゥェォャュョヮ")
_NO_START = set("ンッー")  # a kanji's reading never starts with these
# Characters drawn but not sung: they join the \k of the character before them.
_SILENT = re.compile(r"[\s、。，．,.!?！？「」『』（）()…・~〜\-–—♪\"'“”‘’:：;；]")
_KANA = re.compile(r"[ぁ-ゖァ-ヺー]")
_LATIN = re.compile(r"[A-Za-z]")


# Suno writes what is sung: the particles を・へ・は come back as オ・エ・ワ, and ヅ・ヂ as ズ・ジ.
_SUNG_AS = str.maketrans("ヲヘハヅヂ", "オエワズジ")


def _katakana(s):
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def sung_lines(words):
    """Suno's words as lines of morae: [[(kana, start s, end s), ...], ...].

    Lines are split at newlines as in suno_align.line_spans. A word's span is shared evenly by its characters;
    characters before a newline inside a word belong to the previous line and are held TAIL_HOLD each after
    the previous word.
    """
    lines, prev_end = [], None
    for w in words:
        parts = _TAG.sub("", w["word"]).split("\n")
        start, end = float(w["start_s"]), float(w["end_s"])
        # characters of the previous line that arrived with a word starting the next one (`リ\nト`). A word
        # whose newlines lead nowhere (`オ\n\n[Outro]`, the last word) keeps its own timing
        head = [c for c in parts[0] if not c.isspace()]
        if not any(part.strip() for part in parts[1:]):
            parts = parts[:1]
        if len(parts) > 1 and head and lines:
            t = prev_end if prev_end is not None else start
            for c in head:
                lines[-1].append((c, t, min(t + TAIL_HOLD, start)))
                t = min(t + TAIL_HOLD, start)
            head = []
        body = head
        for part in parts[1:]:
            chars = [c for c in part if not c.isspace()]
            if chars:
                lines.append([])
                body = chars
        if not lines:
            lines.append([])
        if body:
            step = (end - start) / len(body)
            lines[-1] += [(c, start + k * step, start + (k + 1) * step) for k, c in enumerate(body)]
        prev_end = end
    return [_morae(line) for line in lines if line]


def _morae(chars):
    out = []
    for c, a, b in chars:
        if out and _katakana(c) in _SMALL:
            k, a0, _ = out[-1]
            out[-1] = (k + c, a0, b)
        else:
            out.append((c, a, b))
    return out


def _display_units(text):
    """Split a display line into units: ("kana", mora), ("latin", ch), ("run", chars) or ("silent", chars)."""
    units = []
    for c in text:
        if _SILENT.match(c):
            kind = "silent"
        elif _KANA.match(c):
            kind = "kana"
        elif _LATIN.match(c):
            kind = "latin"
        else:
            kind = "run"
        if kind == "kana" and units and units[-1][0] == "kana" and (_katakana(c) in _SMALL):
            units[-1] = ("kana", units[-1][1] + c)
        elif kind in ("run", "silent") and units and units[-1][0] == kind:
            units[-1] = (kind, units[-1][1] + c)
        else:
            units.append((kind, c))
    return units


def _matches(unit, mora):
    kind, s = unit
    if kind == "kana":
        # ヘ and ハ are also ordinary syllables (ヘイ, ハル), so either reading matches
        return _katakana(mora) in (_katakana(s), _katakana(s).translate(_SUNG_AS))
    return s.lower() == mora.lower()  # latin: one letter per sung character


def align_line(text, morae):
    """Time each display character of one line: [(chars, start s, end s), ...], or None if they do not align."""
    units = _display_units(text)
    sung = [u for u in units if u[0] != "silent"]
    n, m = len(sung), len(morae)
    INF = float("inf")
    # best[i][j]: lowest cost aligning the first i sung units with the first j morae; how[i][j]: morae taken
    best = [[INF] * (m + 1) for _ in range(n + 1)]
    how = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i, u in enumerate(sung):
        for j in range(m + 1):
            if best[i][j] == INF:
                continue
            if u[0] == "run":
                for k in range(1, min(MAX_MORAE_PER_CHAR * len(u[1]), m - j) + 1):
                    cost = best[i][j] + (k - 2 * len(u[1])) ** 2
                    if cost < best[i + 1][j + k]:
                        best[i + 1][j + k], how[i + 1][j + k] = cost, k
            elif j < m and _matches(u, morae[j][0]) and best[i][j] < best[i + 1][j + 1]:
                best[i + 1][j + 1], how[i + 1][j + 1] = best[i][j], 1
    if best[n][m] == INF:
        return None
    taken, j = [], m
    for i in range(n, 0, -1):
        taken.append(how[i][j])
        j -= how[i][j]
    taken.reverse()

    segs, j, si = [], 0, 0
    for kind, s in units:
        if kind == "silent":
            if segs:
                tok, a, b = segs[-1]
                segs[-1] = (tok + s, a, b)
            continue
        k = taken[si]
        si += 1
        span = morae[j:j + k]
        j += k
        if kind == "run":
            segs += _split_run(s, span)
        else:
            segs.append((s, span[0][1], span[-1][2]))
    # silent characters at the start of a line join the first sung one
    first = next((i for i, u in enumerate(units) if u[0] != "silent"), len(units))
    lead = "".join(s for kind, s in units[:first])
    if lead and segs:
        tok, a, b = segs[0]
        segs[0] = (lead + tok, a, b)
    return segs


def _split_run(chars, span):
    """Give each character of a kanji run its share of the run's morae (see the module docstring, step 3)."""
    n, k = len(chars), len(span)
    starts = [0]
    allowed = [i for i in range(1, k) if span[i][0][0] not in _NO_START]
    for c in range(1, n):
        target = c * k / n
        options = [i for i in allowed if starts[-1] < i <= k - (n - c)]
        if not options:  # too few morae: share them out in order
            options = [min(starts[-1] + 1, k - 1)]
        starts.append(min(options, key=lambda i: (abs(i - target), i)))
    bounds = starts + [k]
    return [(ch, span[bounds[c]][1], span[max(bounds[c + 1] - 1, bounds[c])][2]) for c, ch in enumerate(chars)]


def lines_to_ass(display_lines, words):
    """Build an SSAFile from the display lines and Suno's words, and the lines to check by ear.

    Raises ValueError listing every line that does not align (and when the line counts differ), so nothing is
    guessed. A character that starts more than GAP_WARN after the one before it is listed: on one song both
    such characters (the last kana of two verbs) were Suno placing the kana 1.2-1.5 s late, fixed by ear.
    """
    sung = sung_lines(words)
    display = [ln.strip() for ln in display_lines if ln.strip()]
    if len(display) != len(sung):
        raise ValueError(f"{len(display)} display lines but Suno sang {len(sung)}. "
                         "Make lyrics.txt one line per sung line")
    subs, failed, warnings = pysubs2.SSAFile(), [], []
    for no, (text, morae) in enumerate(zip(display, sung, strict=True), 1):
        segs = align_line(text, morae)
        if segs is None:
            failed.append(f"  line {no}: {text}  /  sung: {''.join(mo for mo, _, _ in morae)}")
            continue
        for (_, _, prev_end), (tok, a, _) in zip(segs, segs[1:], strict=False):
            if a - prev_end > GAP_WARN:
                warnings.append(f"line {no} ({text}): '{tok.strip()}' starts {a - prev_end:.1f} s after the "
                                f"character before it, at {a:.2f} s. Suno may have placed it late; check by ear")
        parts, t = [], segs[0][1]
        for tok, a, e in segs:
            if a - t > GAP_TOKEN:
                parts.append(("", t, a))
                t = a
            parts.append((tok, t, max(e, t)))
            t = max(e, t)
        cs = [round(parts[0][1] * 100)] + [round(e * 100) for _, _, e in parts]
        text_k = "".join(f"{{\\k{cs[i + 1] - cs[i]}}}{tok}" for i, (tok, _, _) in enumerate(parts))
        subs.append(pysubs2.SSAEvent(start=cs[0] * 10, end=cs[-1] * 10, text=text_k))
    if failed:
        raise ValueError("these lines' kana do not match what Suno sang (fix lyrics.txt or the alignment):\n"
                         + "\n".join(failed))
    return subs, warnings
