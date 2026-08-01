"""align_subtitles.py の照合・ASS生成ロジックのテスト（Whisper呼び出しは含まない）。"""

import os
import sys
import zipfile

import pysubs2
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import align_subtitles as al  # noqa: E402


class FakeWord:
    def __init__(self, word, start, end):
        self.word = word
        self.start = start
        self.end = end


class FakeSegment:
    def __init__(self, words):
        self.words = words


def chars(*specs):
    """(char, start, end) の列を words_to_chars 相当の dict 列にする。"""
    return [{"char": c, "start": s, "end": e} for c, s, e in specs]


class TestWordsToChars:
    def test_distributes_word_duration_evenly(self):
        result = al.words_to_chars([FakeSegment([FakeWord("あい", 1.0, 2.0)])])
        assert result == [
            {"char": "あ", "start": 1.0, "end": 1.5},
            {"char": "い", "start": 1.5, "end": 2.0},
        ]

    def test_skips_segments_without_words(self):
        assert al.words_to_chars([FakeSegment([]), FakeSegment([FakeWord("  ", 0, 1)])]) == []


class TestPlainText:
    def test_strips_tags_and_line_breaks(self):
        assert al.plain_text(r"{\k30}歌\N詞{\i1}だ") == "歌詞だ"

    def test_returns_empty_for_tag_only_event(self):
        assert al.plain_text(r"{\pos(0,0)}") == ""


class TestLoadSourceSubs:
    def test_reads_ass_from_zip(self, tmp_path):
        subs = pysubs2.SSAFile()
        subs.append(pysubs2.SSAEvent(start=0, end=1000, text="歌詞"))
        zip_path = tmp_path / "keyframes.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("subtitles.ass", subs.to_string("ass"))
        assert al.extract_lyric_lines(al.load_source_subs(str(zip_path))) == ["歌詞"]

    def test_errors_when_zip_has_no_subtitles(self, tmp_path):
        zip_path = tmp_path / "keyframes.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("cut_01.png", "x")
        with pytest.raises(SystemExit, match="subtitles.ass"):
            al.load_source_subs(str(zip_path))

    def test_errors_when_missing(self, tmp_path):
        with pytest.raises(SystemExit, match="見つかりません"):
            al.load_source_subs(str(tmp_path / "nope.ass"))

    def test_txt_lyrics_become_karaoke_styled_events(self, tmp_path):
        path = tmp_path / "lyrics.txt"
        path.write_text("一行目\n\n二行目\n", encoding="utf-8")
        subs = al.load_source_subs(str(path))
        assert al.extract_lyric_lines(subs) == ["一行目", "二行目"]
        assert subs.info["PlayResY"] == "1080"
        assert subs.styles["Karaoke"].fontsize == 64

    def test_empty_txt_is_rejected(self, tmp_path):
        path = tmp_path / "lyrics.txt"
        path.write_text("\n\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            al.load_source_subs(str(path))


class TestMapCharsToLines:
    def test_assigns_timings_per_line_ignoring_punctuation(self):
        lines = ["あい、", "うえ"]
        whisper = chars(("あ", 0.0, 0.5), ("い", 0.5, 1.0), ("う", 1.0, 1.5), ("え", 1.5, 2.0))
        result = al.map_chars_to_lines(lines, whisper)
        assert [c["char"] for c in result[0]] == ["あ", "い"]
        assert [c["char"] for c in result[1]] == ["う", "え"]

    def test_ignores_punctuation_detected_by_whisper(self):
        whisper = chars(("あ", 0.0, 0.5), ("、", 0.5, 0.6), ("い", 0.6, 1.0))
        result = al.map_chars_to_lines(["あい"], whisper)
        assert [c["char"] for c in result[0]] == ["あ", "い"]

    def test_raises_on_character_count_mismatch(self):
        with pytest.raises(ValueError, match="文字数が一致しません"):
            al.map_chars_to_lines(["あいう"], chars(("あ", 0.0, 0.5), ("い", 0.5, 1.0)))


class TestBuildKaraokeText:
    def test_assigns_k_tags_from_durations(self):
        text = al.build_karaoke_text("あい", chars(("あ", 0.0, 0.30), ("い", 0.30, 0.75)))
        assert text == r"{\k30}あ{\k45}い"

    def test_punctuation_is_absorbed_by_previous_char(self):
        text = al.build_karaoke_text("あ、い", chars(("あ", 0.0, 0.2), ("い", 0.2, 0.4)))
        assert text == r"{\k20}あ、{\k20}い"

    def test_leading_punctuation_gets_zero_duration(self):
        text = al.build_karaoke_text("「あ", chars(("あ", 0.0, 0.2)))
        assert text == r"{\k0}「{\k20}あ"

    def test_missing_timings_fall_back_to_default_duration(self):
        text = al.build_karaoke_text("あい", chars(("あ", 0.0, 0.2)))
        assert text == rf"{{\k20}}あ{{\k{al.DEFAULT_K_CS}}}い"

    def test_never_emits_zero_duration_for_sung_char(self):
        text = al.build_karaoke_text("あ", chars(("あ", 0.0, 0.001)))
        assert text == r"{\k1}あ"


class TestFillRepeatGaps:
    def test_extends_line_until_just_before_next(self):
        subs = pysubs2.SSAFile()
        subs.append(pysubs2.SSAEvent(start=0, end=1000, text="一"))
        subs.append(pysubs2.SSAEvent(start=5000, end=6000, text="二"))
        al.fill_repeat_gaps(subs)
        assert subs[0].end == 5000 - al.FILL_GAP_MARGIN_MS
        assert subs[1].end == 6000

    def test_leaves_small_gaps_untouched(self):
        subs = pysubs2.SSAFile()
        subs.append(pysubs2.SSAEvent(start=0, end=1000, text="一"))
        subs.append(pysubs2.SSAEvent(start=1050, end=2000, text="二"))
        al.fill_repeat_gaps(subs)
        assert subs[0].end == 1000


class TestBuildAlignedSubs:
    def _orig(self, events):
        subs = pysubs2.SSAFile()
        subs.info["PlayResY"] = "1080"
        subs.styles["Karaoke"] = pysubs2.SSAStyle(fontsize=64)
        for start, end, text in events:
            subs.append(pysubs2.SSAEvent(start=start, end=end, text=text))
        return subs

    def test_keeps_original_start_when_lead_in_is_slightly_early(self):
        orig = self._orig([(1000, 4000, "あ")])
        line_map = {0: chars(("あ", 2.5, 3.0))}
        result = al.build_aligned_subs(orig, line_map, verbose=False)
        # 元ASS 1000ms は Whisper 判定 2500ms より 1500ms 早い（許容範囲内）
        assert result[0].start == 1000

    def test_uses_whisper_start_when_original_is_far_too_early(self):
        orig = self._orig([(0, 9000, "あ")])
        line_map = {0: chars(("あ", 5.0, 5.5))}
        result = al.build_aligned_subs(orig, line_map, verbose=False)
        assert result[0].start == 5000

    def test_uses_whisper_start_when_original_starts_later(self):
        orig = self._orig([(9000, 12000, "あ")])
        line_map = {0: chars(("あ", 5.0, 5.5))}
        result = al.build_aligned_subs(orig, line_map, verbose=False)
        assert result[0].start == 5000

    def test_end_time_extends_by_tail(self):
        orig = self._orig([(0, 1000, "あ"), (8000, 9000, "い")])
        line_map = {0: chars(("あ", 1.0, 1.5)), 1: chars(("い", 6.0, 6.5))}
        result = al.build_aligned_subs(orig, line_map, verbose=False)
        assert result[1].end == 6500 + al.TAIL_MS

    def test_preserves_info_styles_and_untimed_events(self):
        orig = self._orig([(0, 1000, ""), (1000, 2000, "あ")])
        line_map = {0: chars(("あ", 1.0, 1.5))}
        result = al.build_aligned_subs(orig, line_map, verbose=False)
        assert len(result) == 2
        assert result[0].text == ""
        assert result.info["PlayResY"] == "1080"
        assert "Karaoke" in result.styles

    def test_lines_without_timings_are_copied_as_is(self):
        orig = self._orig([(0, 1000, "あ"), (2000, 3000, "い")])
        result = al.build_aligned_subs(orig, {0: chars(("あ", 0.0, 0.5))}, verbose=False)
        assert result[1].text == "い"
