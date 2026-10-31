"""beatmv の純粋ロジック（曲の設定・ドラムの振り分け・歌詞の時刻・SRT）のテスト。

実際の曲の設定（songs/）は git 管理外なので、tests/fixtures/song の架空の曲を使う。描画は打点と
音量を一時ディレクトリに置いて、解像度の倍率だけを確かめる（絵の中身は確かめない）。
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
    assert s.sections[0][0] == 0.0, "最初のセクションは 0 秒から始まる（見出しが空になる）"
    assert [t for t, _ in s.sections] == sorted(t for t, _ in s.sections)
    assert s.instrumental[-1][1] == pytest.approx(s.duration), "最後の歌のない区間は曲の終わりまで（credit を出す）"
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


# --------------------------------------------------------------------------- ドラムの振り分け

def test_rock_rule_needs_low_to_dominate_for_kick():
    t = np.array([1.0, 2.0, 3.0])
    lr = np.array([3.0, 3.0, 0.5])   # 1: 低域が主役 / 2: 中高域も同じだけ立つ / 3: 低域が立たない
    mr = np.array([2.0, 3.5, 2.0])
    kick, perc = drums.classify(t, lr, mr, "rock")
    assert list(kick) == [1.0]
    assert list(perc) == [1.0, 2.0, 3.0]  # 同時打ちはスネアにも出てよい


def test_edm_rule_keeps_kicks_whose_attack_reaches_the_mids():
    # 四つ打ちのキックはアタックが 1-4kHz にも出る。rock の基準ではキックが落ちた（84 回しか残らない）
    t = np.array([1.0, 2.0, 3.0])
    lr = np.array([3.2, 2.4, -0.9])  # 1・2: キック / 3: 裏拍のハット
    mr = np.array([3.4, 4.8, 1.3])
    kick, _ = drums.classify(t, lr, mr, "rock")
    assert list(kick) == []
    kick, perc = drums.classify(t, lr, mr, "edm")
    assert list(kick) == [1.0, 2.0]
    assert list(perc) == [2.0, 3.0]  # キックの分を超えて中高域が立った打点だけ（クラップ・ハット）


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        drums.classify([1.0], [1.0], [1.0], "jazz")


# --------------------------------------------------------------------------- 歌詞

def test_fixture_lyrics_k_sums_fit_the_line():
    song_dir = FIXTURE
    # Suno の時刻合わせから作った ASS は \k の合計が行の長さと一致する。align_subtitles.py の ASS は
    # 行末を次行の直前まで延ばし、逆に次行と重なった行末は \k を残したまま切る（trim_overlaps）ので、
    # 合計は行の長さから余韻（TAIL_MS）以上はみ出さなければよい
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
    # align_subtitles.py は行の開始を前の行の終わりまで繰り上げ、その分を最初の文字に入れる
    ass = write_ass(tmp_path / "a.ass", [(37540, 42380, r"{\k156}駆{\k20}け")])
    (_, _, _, segs), = lyrics.load_lines(str(ass), "whisper")
    assert segs[0][1] == pytest.approx(39.10 - lyrics.FIRST_SEG_WIPE)
    assert segs[0][2] == pytest.approx(39.10)
    (_, _, _, segs), = lyrics.load_lines(str(ass), "suno")
    assert segs[0][1] == pytest.approx(37.54), "Suno の時刻合わせは行の開始が歌い出しなので補正しない"


def test_short_first_segment_is_left_alone(tmp_path):
    ass = write_ass(tmp_path / "a.ass", [(1000, 2000, r"{\k30}a{\k70}b")])
    (_, _, _, segs), = lyrics.load_lines(str(ass), "whisper")
    assert segs[0][1] == pytest.approx(1.0)


def test_progress_fills_each_token_over_its_own_length():
    segs = [("a", 0.0, 1.0), ("b", 1.0, 4.0)]  # b は 3 秒伸ばす
    assert lyrics.progress(segs, -0.1) == 0.0
    assert lyrics.progress(segs, 0.5) == pytest.approx(0.5)
    assert lyrics.progress(segs, 2.5) == pytest.approx(1.5)
    assert lyrics.progress(segs, 9.0) == pytest.approx(2.0)


def test_line_disappears_during_a_long_gap():
    lines = [("a", 0.0, 2.0, []), ("b", 20.0, 22.0, [])]
    assert lyrics.current_line(lines, 3.0, 1.5) == 0
    assert lyrics.current_line(lines, 10.0, 1.5) is None, "間奏の間に前の行が残らない"
    assert lyrics.current_line(lines, 19.9, 1.5) == 1, "次の行は 0.25 秒前に出す"


def test_srt_starts_at_first_sung_token_and_never_overlaps(tmp_path):
    lines = [("a", 0.0, 5.0, [("a", 1.2, 4.0)]), ("b", 5.0, 9.0, [("b", 4.6, 8.0)])]
    subs = lyrics.to_srt(lines, str(tmp_path / "x.srt"))
    assert [(e.start, e.end) for e in subs] == [(1200, 4600), (4600, 9000)]


# --------------------------------------------------------------------------- 描画の倍率

@pytest.mark.parametrize("scale", [1, 2])
def test_render_scales_the_whole_frame(tmp_path, scale):
    # analyze の出力（打点と音量）を一時ディレクトリに置き、架空の曲をサビの途中で描く
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


# --------------------------------------------------------------------------- 配色（styles/ を burn_subs と共有）

STYLES = sorted(f for f in os.listdir(os.path.join(REPO, "styles")) if f.endswith(".json"))


@pytest.mark.parametrize("name", STYLES)
def test_every_style_preset_loads_cleanly_in_burn_subs(name, capsys):
    path = os.path.join(REPO, "styles", name)
    loaded = burn_subs.load_style_file(path)
    with open(path, encoding="utf-8") as f:
        assert loaded == json.load(f), "不正値として落とされたキーがある"
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
    assert pal.outline == (11, 26, 58), "書いていない色は karaoke_blue の色"
    (tmp_path / "bad.json").write_text(json.dumps({"accent_color": "red"}))
    with pytest.raises(ValueError):
        style.load("bad")


def test_unknown_style_in_song_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        songmod.load(write_song(tmp_path, style='"no_such_style"'))
