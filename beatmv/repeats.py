"""Map the lines Suno sang onto the lines as written, when Suno sang some of them again.

Suno fills the requested duration. When a take's vocal starts early, it can sing the last chorus (or the build and
the last chorus) a second time to fill the length: 6 of 34 takes of one song did (2026-11-27). The written lyrics
(lyrics.txt) and the translation (translation.txt) have one row per written line, so a repeated block used to stop
`align` and `load_translation` until the rows were copied by hand.

map_repeats explains each sung line as a written line. The written lines are matched in order (longest common
subsequence); a sung line left over is a repeat when it matches a written line at or before the last matched one.
Every written line must be sung and every sung line explained, or nothing is guessed and the caller keeps its
"counts differ" error.

The result travels in lyrics.ass's [Script Info] (LineSources: the written line of each event, LineSourcesOf: how
many written lines there are), so load_translation can give a repeated line the translation of the line it repeats.
"""

SOURCES_KEY = "LineSources"
SOURCES_OF_KEY = "LineSourcesOf"


def map_repeats(n_written, n_sung, same):
    """Return, for each sung line, the index of the written line it sings, or None if that cannot be explained.

    same(i, j) says whether sung line j sings written line i.
    """
    cache = {}

    def eq(i, j):
        if (i, j) not in cache:
            cache[(i, j)] = bool(same(i, j))
        return cache[(i, j)]

    # lcs[i][j]: longest common subsequence of written[i:] and sung[j:]
    lcs = [[0] * (n_sung + 1) for _ in range(n_written + 1)]
    for i in range(n_written - 1, -1, -1):
        for j in range(n_sung - 1, -1, -1):
            if eq(i, j):
                lcs[i][j] = lcs[i + 1][j + 1] + 1
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])
    if lcs[0][0] != n_written:
        return None  # a written line was not sung

    pairs = [None] * n_sung
    i = j = 0
    while i < n_written and j < n_sung:
        if eq(i, j) and lcs[i][j] == lcs[i + 1][j + 1] + 1:
            pairs[j] = i
            i, j = i + 1, j + 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            i += 1
        else:
            j += 1

    last = -1
    for j in range(n_sung):
        if pairs[j] is not None:
            last = pairs[j]
            continue
        at = next((k for k in range(last, -1, -1) if eq(k, j)), None)
        if at is None:
            return None  # a sung line that is neither written nor a repeat
        pairs[j] = at
    return pairs


def repeated_runs(sources):
    """The repeated blocks as (first sung line, last sung line, first written line), 1-based, for messages."""
    runs, seen = [], set()
    for j, i in enumerate(sources):
        repeat = i in seen
        seen.add(i)
        if not repeat:
            continue
        if runs and runs[-1][1] == j and sources[j - 1] == i - 1:
            runs[-1] = (runs[-1][0], j + 1, runs[-1][2])
        else:
            runs.append((j + 1, j + 1, i + 1))
    return runs


def describe(sources):
    """One message per repeated block, for the align output."""
    out = []
    for first, last, written in repeated_runs(sources):
        span = f"line {first}" if first == last else f"lines {first}-{last}"
        out.append(f"{span} repeat written line {written} onward: Suno sang them again; the subtitles and the "
                   "translation follow what was sung")
    return out


def store(subs, sources, n_written):
    """Record the mapping in the ASS header when some line was sung more than once."""
    if sources is None or len(set(sources)) == len(sources):
        return
    subs.info[SOURCES_KEY] = ",".join(str(i) for i in sources)
    subs.info[SOURCES_OF_KEY] = str(n_written)


def load(subs):
    """The mapping stored by store, as (sources, number of written lines), or None."""
    raw, of = subs.info.get(SOURCES_KEY), subs.info.get(SOURCES_OF_KEY)
    if not raw or not of:
        return None
    try:
        return [int(x) for x in raw.split(",")], int(of)
    except ValueError:
        return None


def normalise(text):
    """Text compared for English lines: letters and digits only, lower case."""
    return "".join(c for c in text.lower() if c.isalnum())
