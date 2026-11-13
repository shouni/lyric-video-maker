"""Tests for burn_subs_video.py's display spans, events and transparent overlay track (no ffmpeg)."""

import os
import sys

import pysubs2
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import burn_subs as bs  # noqa: E402
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


class TestBuildEvents:
    def test_keeps_karaoke_tags_and_sorts_by_start(self):
        subs = make_subs([(3000, 4000, r"{\k10}後"), (0, 500, ""), (1000, 2000, r"{\k50}先")])
        assert bsv.build_events(subs) == [(1.0, 2.0, r"{\k50}先"), (3.0, 4.0, r"{\k10}後")]


class TestBuildOverlayTrack:
    """The transparent subtitle track laid over the video (draws with a real font)."""

    def cfg(self, mode):
        font = bs.load_font(40)
        return bs.RenderConfig(
            font=font, img_size=(320, 180), margin_v=10, outline=1,
            primary=(255, 255, 0), secondary=(255, 255, 255), outline_color=(0, 0, 0), mode=mode,
        )

    def track(self, tmp_path, mode, video_dur=5.0):
        events = [(1.0, 2.0, r"{\k50}あ{\k50}い"), (3.0, 4.0, r"{\k100}う")]
        lines = bsv.build_overlay_track(events, self.cfg(mode), video_dur, str(tmp_path))
        files = [ln.split("'")[1] for ln in lines if ln.startswith("file ")]
        durations = [float(ln.split()[1]) for ln in lines if ln.startswith("duration ")]
        return files, durations

    def test_line_mode_has_one_state_per_line_and_runs_the_length_of_the_video(self, tmp_path):
        files, durations = self.track(tmp_path, "line")
        assert sum(durations) == pytest.approx(5.0)
        assert durations == pytest.approx([1.0, 1.0, 1.0, 1.0, 1.0])
        assert files[0] == files[2] == files[4], "the empty layer is drawn once and reused between lines"
        assert files[1] != files[3]

    def test_frames_are_transparent_away_from_the_text(self, tmp_path):
        files, _ = self.track(tmp_path, "line")
        blank, line = Image.open(files[0]), Image.open(files[1])
        assert blank.mode == line.mode == "RGBA"
        assert blank.getchannel("A").getbbox() is None, "between lines the video shows through everywhere"
        assert line.getchannel("A").getbbox() is not None
        assert line.getpixel((2, 2))[3] == 0

    def test_karaoke_mode_adds_a_state_per_highlighted_character(self, tmp_path):
        files, durations = self.track(tmp_path, "karaoke")
        assert durations == pytest.approx([1.0, 0.5, 0.5, 1.0, 1.0, 1.0])
        assert len(set(files)) == 4  # empty, あ sung, あい sung, う sung

    def test_track_covers_subtitles_that_run_past_the_video(self, tmp_path):
        _, durations = self.track(tmp_path, "line", video_dur=0.0)
        assert sum(durations) == pytest.approx(4.0)
