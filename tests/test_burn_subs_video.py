"""burn_subs_video.py の表示区間計算のテスト。"""

import os
import sys

import pysubs2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import burn_subs_video as bsv  # noqa: E402


def make_subs(events):
    subs = pysubs2.SSAFile()
    for start_ms, end_ms, text in events:
        subs.append(pysubs2.SSAEvent(start=start_ms, end=end_ms, text=text))
    return subs


class TestBuildWindows:
    def test_strips_karaoke_tags_and_converts_to_seconds(self):
        subs = make_subs([(1000, 2500, r"{\k50}あ{\k100}い")])
        assert bsv.build_windows(subs) == [(1.0, 2.5, "あい")]

    def test_skips_empty_events(self):
        subs = make_subs([(0, 1000, ""), (1000, 2000, r"{\k10}歌詞")])
        assert bsv.build_windows(subs) == [(1.0, 2.0, "歌詞")]

    def test_earlier_line_wins_on_overlap(self):
        subs = make_subs([(0, 3000, "先"), (2000, 4000, "後")])
        assert bsv.build_windows(subs) == [(0.0, 3.0, "先"), (3.0, 4.0, "後")]

    def test_drops_fully_covered_line(self):
        subs = make_subs([(0, 5000, "先"), (1000, 4000, "内側")])
        assert bsv.build_windows(subs) == [(0.0, 5.0, "先")]

    def test_sorts_out_of_order_events(self):
        subs = make_subs([(3000, 4000, "後"), (1000, 2000, "先")])
        assert bsv.build_windows(subs) == [(1.0, 2.0, "先"), (3.0, 4.0, "後")]
