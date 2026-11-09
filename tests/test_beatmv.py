"""Tests for beatmv's pure logic (song settings, drum rules, lyric timing, SRT).

Real song settings (songs/) are not in git, so a made-up song in tests/fixtures/song is used. Rendering puts
hits and loudness in a temporary directory and checks only the resolution scale (not the picture itself).
"""

import json
import os
import sys

import numpy as np
import pysubs2
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import align_subtitles  # noqa: E402
import burn_subs  # noqa: E402
from beatmv import drums, lyrics, song as songmod  # noqa: E402

FIXTURE = os.path.join(REPO, "tests", "fixtures", "song")


def write_ass(path, events):
    subs = pysubs2.SSAFile()
    for start, end, text in events:
        subs.append(pysubs2.SSAEvent(start=start, end=end, text=text))
    subs.save(path)
    return path


def write_song(dir_, **over):
    fields = {
        "title": '"t"', "audio": '"work/x/a.wav"', "work_dir": '"work/x"', "duration": "10.0",
        "lyrics_aligner": '"suno"', "drum_rule": '"rock"',
        "sections": '[[0.0, "INTRO"]]', "choruses": "[]", "instrumental": "[[0.0, 1.0]]",
    }
    fields.update(over)
    with open(os.path.join(dir_, "song.toml"), "w") as f:
        f.write("\n".join(f"{k} = {v}" for k, v in fields.items()))
    return str(dir_)


# --------------------------------------------------------------------------- song.toml

def test_fixture_song_loads():
    s = songmod.load(FIXTURE)
    assert s.title and s.duration > 0
    assert s.sections[0][0] == 0.0, "the first section starts at 0 s (otherwise the header is empty)"
    assert [t for t, _ in s.sections] == sorted(t for t, _ in s.sections)
    assert s.instrumental[-1][1] == pytest.approx(s.duration), "the last section without singing runs to the end (shows the credit)"
    lo, hi = s.thumbnail_window
    assert 0 <= lo < hi <= s.duration


def test_song_paths_resolve_against_repo_and_song_dir(tmp_path):
    s = songmod.load(write_song(tmp_path))
    assert s.audio == os.path.join(songmod.REPO, "work/x/a.wav")
    assert s.lyrics == os.path.join(str(tmp_path), "lyrics.ass")
    assert s.perc_label == "SNARE"


def test_edm_defaults_perc_label(tmp_path):
    assert songmod.load(write_song(tmp_path, drum_rule='"edm"')).perc_label == "PERC"


@pytest.mark.parametrize("field,value", [("drum_rule", '"jazz"'), ("lyrics_aligner", '"manual"')])
def test_unknown_choices_are_rejected(tmp_path, field, value):
    with pytest.raises(ValueError):
        songmod.load(write_song(tmp_path, **{field: value}))


def test_bass_band_is_off_unless_asked(tmp_path):
    # the bass band only shows when song.toml enables it, so existing songs look the same
    assert songmod.load(write_song(tmp_path)).bass_band is False
    assert songmod.load(write_song(tmp_path, bass_band="true")).bass_band is True


# --------------------------------------------------------------------------- drum rules

def test_rock_rule_needs_low_to_dominate_for_kick():
    t = np.array([1.0, 2.0, 3.0])
    lr = np.array([3.0, 3.0, 0.5])   # 1: low end leads / 2: mids rise as much / 3: no low end
    mr = np.array([2.0, 3.5, 2.0])
    kick, perc = drums.classify(t, lr, mr, "rock")
    assert list(kick) == [1.0]
    assert list(perc) == [1.0, 2.0, 3.0]  # simultaneous hits may show as snares too


def test_edm_rule_keeps_kicks_whose_attack_reaches_the_mids():
    # a four-on-the-floor kick's attack reaches 1–4 kHz; the rock rule lost kicks (only 84 left)
    t = np.array([1.0, 2.0, 3.0])
    lr = np.array([3.2, 2.4, -0.9])  # 1, 2: kicks / 3: off-beat hat
    mr = np.array([3.4, 4.8, 1.3])
    kick, _ = drums.classify(t, lr, mr, "rock")
    assert list(kick) == []
    kick, perc = drums.classify(t, lr, mr, "edm")
    assert list(kick) == [1.0, 2.0]
    assert list(perc) == [2.0, 3.0]  # only hits whose mids rose beyond the kick (claps, hats)


def test_trance_rule_does_not_count_a_kick_as_perc():
    # with edm the kick's attack also met the percussion condition, so hits showed in both rows (131 / 258 on 夜明けの境界線)
    t = np.array([1.0, 2.0, 3.0])
    lr = np.array([3.2, 2.4, -0.9])  # 1, 2: kicks / 3: off-beat hat
    mr = np.array([3.4, 4.8, 1.3])
    kick, perc = drums.classify(t, lr, mr, "trance")
    assert list(kick) == [1.0, 2.0]
    assert list(perc) == [3.0]


def test_dnb_rule_keeps_kicks_out_of_the_snare_row_and_drops_ghost_notes():
    # DnB kicks have a hard attack; with rock the downbeat kick also showed as a snare (43–89 times over six songs)
    t = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    lr = np.array([3.0, 0.2, 0.1, 0.3, 0.2, 0.1])  # 1: kick / 2–6: snare-band hits
    mr = np.array([2.0, 3.5, 3.4, 1.2, 3.6, 1.1])  # 4, 6: ghost notes with a weak attack
    kick, perc = drums.classify(t, lr, mr, "rock")
    assert 1.0 in perc  # with rock, the kick also shows as a snare
    kick, perc = drums.classify(t, lr, mr, "dnb")
    assert list(kick) == [1.0]
    assert list(perc) == [2.0, 3.0, 5.0]


def test_dnb_split_keeps_the_dropped_ghost_notes():
    # dropped ghost notes would "play but not show", so they come back as a separate list
    t = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    lr = np.array([3.0, 0.2, 0.1, 0.3, 0.2, 0.1])
    mr = np.array([2.0, 3.5, 3.4, 1.2, 3.6, 1.1])
    kick, snare, ghost = drums.dnb_split(t, lr, mr)
    assert list(kick) == [1.0]
    assert list(snare) == [2.0, 3.0, 5.0]
    assert list(ghost) == [4.0, 6.0]


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        drums.classify([1.0], [1.0], [1.0], "jazz")


# --------------------------------------------------------------------------- lyrics

def test_fixture_lyrics_k_sums_fit_the_line():
    song_dir = FIXTURE
    # In an ASS built from Suno's alignment, the \k total equals the line length. align_subtitles.py extends
    # line ends up to the next line and cuts overlapping ends while keeping the \k (trim_overlaps), so the
    # total only needs to stay within the tail (TAIL_MS) of the line length
    exact = songmod.load(song_dir).lyrics_aligner == "suno"
    for e in pysubs2.load(os.path.join(song_dir, "lyrics.ass")):
        segs = burn_subs.parse_karaoke(e.text)
        assert segs, e.text
        total, length = segs[-1][1], (e.end - e.start) / 10
        if exact:
            assert abs(total - length) <= 1, e.text
        else:
            assert total <= length + align_subtitles.TAIL_MS / 10, e.text


def test_whisper_lines_start_wiping_near_the_end_of_a_long_first_segment(tmp_path):
    # align_subtitles.py pulls the line start back to the previous line's end and puts that time in the first character
    ass = write_ass(tmp_path / "a.ass", [(37540, 42380, r"{\k156}駆{\k20}け")])
    (_, _, _, segs), = lyrics.load_lines(str(ass), "whisper")
    assert segs[0][1] == pytest.approx(39.10 - lyrics.FIRST_SEG_WIPE)
    assert segs[0][2] == pytest.approx(39.10)
    (_, _, _, segs), = lyrics.load_lines(str(ass), "suno")
    assert segs[0][1] == pytest.approx(37.54), "Suno's alignment starts the line where it is sung, so no correction"


def test_short_first_segment_is_left_alone(tmp_path):
    ass = write_ass(tmp_path / "a.ass", [(1000, 2000, r"{\k30}a{\k70}b")])
    (_, _, _, segs), = lyrics.load_lines(str(ass), "whisper")
    assert segs[0][1] == pytest.approx(1.0)


def test_progress_fills_each_token_over_its_own_length():
    segs = [("a", 0.0, 1.0), ("b", 1.0, 4.0)]  # b is held for 3 s
    assert lyrics.progress(segs, -0.1) == 0.0
    assert lyrics.progress(segs, 0.5) == pytest.approx(0.5)
    assert lyrics.progress(segs, 2.5) == pytest.approx(1.5)
    assert lyrics.progress(segs, 9.0) == pytest.approx(2.0)


def test_line_disappears_during_a_long_gap():
    lines = [("a", 0.0, 2.0, []), ("b", 20.0, 22.0, [])]
    assert lyrics.current_line(lines, 3.0, 1.5) == 0
    assert lyrics.current_line(lines, 10.0, 1.5) is None, "the previous line does not stay during the interlude"
    assert lyrics.current_line(lines, 19.9, 1.5) == 1, "the next line appears 0.25 s early"


def test_srt_starts_at_first_sung_token_and_never_overlaps(tmp_path):
    lines = [("a", 0.0, 5.0, [("a", 1.2, 4.0)]), ("b", 5.0, 9.0, [("b", 4.6, 8.0)])]
    subs = lyrics.to_srt(lines, str(tmp_path / "x.srt"))
    assert [(e.start, e.end) for e in subs] == [(1200, 4600), (4600, 9000)]


# --------------------------------------------------------------------------- render scale

@pytest.mark.parametrize("scale", [1, 2])
def test_render_scales_the_whole_frame(tmp_path, scale):
    # put analyze's output (hits and loudness) in a temp directory and draw the made-up song mid-chorus
    work = tmp_path / "work"
    work.mkdir()
    (work / "drums.json").write_text(json.dumps({"kick": [6.0, 6.5, 7.0], "snare": [6.5]}))
    np.save(work / "rms.npy", np.full(12 * 30, 0.5))
    song_dir = tmp_path / "song"
    song_dir.mkdir()
    toml = open(os.path.join(FIXTURE, "song.toml")).read().replace('"work/fixture"', f'"{work}"')
    (song_dir / "song.toml").write_text(toml)
    for name in ("beats.json", "lyrics.ass"):
        (song_dir / name).write_bytes(open(os.path.join(FIXTURE, name), "rb").read())

    from beatmv import render

    render.configure(str(song_dir), scale)
    img = render.render_image(7.1)
    assert img.size == (1920 * scale, 1080 * scale)


# --------------------------------------------------------------------------- colours (styles/ shared with burn_subs)

STYLES = sorted(f for f in os.listdir(os.path.join(REPO, "styles")) if f.endswith(".json"))


@pytest.mark.parametrize("name", STYLES)
def test_every_style_preset_loads_cleanly_in_burn_subs(name, capsys):
    path = os.path.join(REPO, "styles", name)
    loaded = burn_subs.load_style_file(path)
    with open(path, encoding="utf-8") as f:
        assert loaded == json.load(f), "some key was dropped as invalid"
    assert "Warning" not in capsys.readouterr().err


@pytest.mark.parametrize("name", STYLES)
def test_every_style_preset_gives_beatmv_a_palette(name):
    from beatmv import style

    style.load(name[:-len(".json")])


def test_karaoke_blue_is_the_default_palette():
    from beatmv import style

    pal = style.load("karaoke_blue")
    assert pal.sung == pal.accent == (143, 227, 255)
    assert pal.unsung == (255, 255, 255)
    assert pal.outline == (11, 26, 58)
    assert pal.background_top == (4, 9, 26)
    assert songmod.load(FIXTURE).style == "karaoke_blue"


def test_accent_defaults_to_primary_and_bad_colors_fail(tmp_path, monkeypatch):
    from beatmv import style

    monkeypatch.setattr(style, "style_path", lambda name: str(tmp_path / f"{name}.json"))
    (tmp_path / "red.json").write_text(json.dumps({"primary_color": "#CC0000", "outline": 6}))
    pal = style.load("red")
    assert pal.accent == pal.sung == (204, 0, 0)
    assert pal.outline == (11, 26, 58), "colours not given fall back to karaoke_blue"
    (tmp_path / "bad.json").write_text(json.dumps({"accent_color": "red"}))
    with pytest.raises(ValueError):
        style.load("bad")


def test_unknown_style_in_song_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        songmod.load(write_song(tmp_path, style='"no_such_style"'))


# --------------------------------------------------------------------------- hybrid（Suno + Whisper）

def suno_word(word, start, end):
    return {"word": word, "start_s": start, "end_s": end, "success": True, "p_align": 1}


# shape of Suno's audio-lyrics-alignment: a newline at the start of a word breaks the line; section tags are mixed into words
SUNO_WORDS = [
    suno_word("[Intro: instrumental only]\n\n\n[Verse: sparse breakbeat]\nカ", 16.46, 16.65),
    suno_word("ケル", 16.65, 19.37),
    suno_word("\nOver", 58.90, 59.29),
    suno_word("clock", 59.29, 60.46),
    suno_word("k\n\n[Outro: instrumental only]\n\n\n[End]\n", 60.46, 61.00),
]


def test_suno_line_spans_follow_newlines_and_ignore_tags():
    from beatmv import suno_align
    spans = suno_align.line_spans(SUNO_WORDS)
    assert spans == [(16.46, 19.37), (58.90, 61.00)], "no line break after a tag; the last word stays in the previous line"


def test_hybrid_keeps_whisper_shares_inside_the_suno_span(tmp_path):
    from beatmv import suno_align
    # Whisper: line 1's first character is 2 s (the previous line's tail); line 2's last character runs 3 s into the interlude
    ass = write_ass(tmp_path / "w.ass", [
        (14000, 18300, r"{\k200}駆{\k150}け{\k80}る"),
        (57000, 62000, r"{\k50}Over{\k150}clock{\k300}!"),
    ])
    subs, warnings = suno_align.merge(str(ass), SUNO_WORDS)

    lines = lyrics.load_lines_from(subs, "hybrid")
    (_, s1, e1, segs1), (_, s2, e2, segs2) = lines
    assert (s1, e1) == pytest.approx((16.46, 19.37), abs=0.01), "the line span comes from Suno"
    assert (s2, e2) == pytest.approx((58.90, 61.00), abs=0.01)
    # line 1: trim only the first character to 0.4 s, stretch the rest keeping Whisper's shares (0.4 : 1.5 : 0.8)
    widths = [b - a for _, a, b in segs1]
    assert widths[1] / widths[0] == pytest.approx(1.5 / 0.4, rel=0.05)
    assert widths[2] / widths[1] == pytest.approx(0.8 / 1.5, rel=0.05)
    # line 2: trim the 3 s last character to 1 s before mapping
    widths = [b - a for _, a, b in segs2]
    assert widths[2] / widths[1] == pytest.approx(1.0 / 1.5, rel=0.05)
    assert any("line 1 " in w for w in warnings), "a long hold left after merging is flagged for checking"


def test_hybrid_refuses_when_line_counts_differ(tmp_path):
    from beatmv import suno_align
    ass = write_ass(tmp_path / "w.ass", [(1000, 2000, r"{\k100}a")])
    with pytest.raises(ValueError, match="line counts differ"):
        suno_align.merge(str(ass), SUNO_WORDS)


def test_hybrid_is_an_accepted_aligner_and_skips_the_whisper_wipe(tmp_path):
    songmod.load(write_song(tmp_path, lyrics_aligner='"hybrid"'))
    ass = write_ass(tmp_path / "a.ass", [(37540, 42380, r"{\k156}駆{\k20}け")])
    (_, _, _, segs), = lyrics.load_lines(str(ass), "hybrid")
    assert segs[0][1] == pytest.approx(37.54), "hybrid is already trimmed by align, so it is not trimmed again"


# --------------------------------------------------------------------------- hook lines

@pytest.mark.parametrize("text,hook", [
    ("Override! Override!", True),
    ("Overclock the heart", True),
    ("Armor Reboot!", True),
    ("Hard Reset! Hard Reset!", True),
    ("Afterglow, 鳴り止まないよ", False),       # a line with Japanese is not a hook shout
    ("Run through the night and never stop", False),  # an English line of more than four words
    ("もっと、もっと", False),
    ("！？", False),
])
def test_hook_lines_are_short_english_shouts(text, hook):
    assert lyrics.is_hook_line(text) is hook


def test_hook_color_defaults_to_cyan():
    from beatmv import style
    assert style.load("karaoke_blue").hook == (0x00, 0xE5, 0xFF)


# --------------------------------------------------------------------------- waveform

def test_wave_defaults_to_mix_and_rejects_unknown(tmp_path):
    assert songmod.load(write_song(tmp_path)).wave == "mix", "existing songs look the same when rebuilt"
    assert songmod.load(write_song(tmp_path, wave='"vocals"')).wave == "vocals"
    with pytest.raises(ValueError, match="wave"):
        songmod.load(write_song(tmp_path, wave='"drums"'))


def test_hook_weight_eases_in_and_out_around_hook_lines(monkeypatch):
    from types import SimpleNamespace

    from beatmv import render
    monkeypatch.setattr(render, "C", SimpleNamespace(hook_spans=[(10.0, 12.0)]))
    fade = render.HOOK_FADE
    assert render.hook_weight(10.0 - fade) == pytest.approx(0.0)
    assert render.hook_weight(10.0 - fade / 2) == pytest.approx(0.5), "starts shifting HOOK_FADE s before"
    assert render.hook_weight(11.0) == pytest.approx(1.0)
    assert render.hook_weight(12.0 + fade / 2) == pytest.approx(0.5), "shifts back over HOOK_FADE s after it is sung"
    assert render.hook_weight(20.0) == 0.0


def test_mix_rgb_blends_between_two_colors():
    from beatmv import render
    assert render.mix_rgb((0, 0, 0), (100, 200, 50), 0.5) == (50, 100, 25)


# --------------------------------------------------------------------------- English subtitles (Suno only) and translations

ENGLISH_WORDS = [
    suno_word("[Verse: light breaks]\nStatic", 34.689, 35.334),
    suno_word(" is", 35.354, 35.55),
    suno_word(" gone", 35.55, 35.987),
    suno_word(" from", 37.535, 37.942),
    suno_word(" the roof", 38.0, 38.5),
    suno_word("s\nColor", 41.0, 41.4),  # a word spanning a newline: "s" goes to the previous line, "Color" to the next
    suno_word(" glows\n\n[Outro: instrumental]\n\n[End]\n", 41.4, 42.0),
]


def test_suno_words_become_word_timed_lines():
    from beatmv import suno_align
    lines = lyrics.load_lines_from(suno_align.words_to_ass(ENGLISH_WORDS), "suno")
    assert [t for t, *_ in lines] == ["Static is gone from the roofs", "Color glows"]
    (_, s, _, segs), (_, s2, e2, _) = lines
    assert s == pytest.approx(34.69, abs=0.01) and (s2, e2) == pytest.approx((41.0, 42.0), abs=0.01)
    # the 1.5 s between "gone" and "from" becomes an empty \k; "from" starts filling when it is sung
    frm = next(seg for seg in segs if seg[0] == " from")
    assert frm[1] == pytest.approx(37.53, abs=0.01)


def test_suno_punctuation_is_painted_with_the_word_before_it():
    # Suno attaches the "!" of "standing!" to the next word. Unless it fills with the word before, it stays white until the next word
    from beatmv import suno_align
    words = [{"word": "Still", "start_s": 73.19, "end_s": 73.52}, {"word": " standing", "start_s": 73.52, "end_s": 74.36},
             {"word": "! Still", "start_s": 78.64, "end_s": 79.04}, {"word": " standing", "start_s": 79.04, "end_s": 79.68},
             {"word": "!", "start_s": 79.68, "end_s": 79.7}]
    (_, _, _, segs), = lyrics.load_lines_from(suno_align.words_to_ass(words), "suno")
    assert [seg[0] for seg in segs if seg[0]] == ["Still", " standing!", " Still", " standing!"]
    bang = next(seg for seg in segs if seg[0] == " standing!")
    assert bang[2] == pytest.approx(74.36, abs=0.01)


def write_translation_song(tmp_path, rows):
    (tmp_path / "ja.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return songmod.load(write_song(tmp_path, translation='"ja.txt"'))


def test_translation_needs_one_row_per_line(tmp_path):
    s = write_translation_song(tmp_path, ["ノイズが消えた", "", "色が光る"])
    assert songmod.load_translation(s, 3) == ["ノイズが消えた", "", "色が光る"], "an empty row means no translation for that line"
    with pytest.raises(ValueError, match="one row per line"):
        songmod.load_translation(s, 2)


def test_trailing_blank_rows_still_count(tmp_path):
    s = write_translation_song(tmp_path, ["Charge ahead", "", ""])
    assert songmod.load_translation(s, 3) == ["Charge ahead", "", ""], "the count holds even when the last lines are English"


def test_translation_is_off_unless_asked(tmp_path):
    s = songmod.load(write_song(tmp_path))
    assert s.translation == "" and songmod.load_translation(s, 5) is None


def test_render_draws_the_translation_under_the_lyric(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "drums.json").write_text(json.dumps({"kick": [6.0], "snare": [6.5]}))
    np.save(work / "rms.npy", np.full(12 * 30, 0.5))
    song_dir = tmp_path / "song"
    song_dir.mkdir()
    toml = open(os.path.join(FIXTURE, "song.toml")).read().replace('"work/fixture"', f'"{work}"')
    n = len(pysubs2.load(os.path.join(FIXTURE, "lyrics.ass")))
    # put it before the [thumbnail] table (after it, it would become a thumbnail key)
    (song_dir / "song.toml").write_text('translation = "ja.txt"\n' + toml)
    (song_dir / "ja.txt").write_text("\n".join(["訳"] * n) + "\n", encoding="utf-8")
    for name in ("beats.json", "lyrics.ass"):
        (song_dir / name).write_bytes(open(os.path.join(FIXTURE, name), "rb").read())

    from beatmv import render

    render.configure(str(song_dir), 1)
    with_trans = np.asarray(render.render_image(7.1))
    render.C.translation = [""] * n
    without = np.asarray(render.render_image(7.1))
    band = slice(render.C.LYRIC_Y + 40, render.C.LYRIC_Y + 160)
    assert (with_trans[band] != without[band]).any(), "the translation is drawn in the band under the lyric"
