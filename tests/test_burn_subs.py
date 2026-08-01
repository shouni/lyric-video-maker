"""burn_subs.py の純粋関数（描画・ffmpeg を伴わない部分）のテスト。"""

import json
import os
import sys
import zipfile

import pysubs2
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import burn_subs as bs  # noqa: E402


def make_subs(fontsize=64, play_res_y=1080, style_name="Karaoke"):
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(round(play_res_y * 16 / 9))
    subs.info["PlayResY"] = str(play_res_y)
    subs.styles[style_name] = pysubs2.SSAStyle(
        fontname="Arial",
        fontsize=fontsize,
        primarycolor=pysubs2.Color(255, 255, 0),
        secondarycolor=pysubs2.Color(255, 255, 255),
        outlinecolor=pysubs2.Color(0, 0, 0),
        outline=3,
        marginv=80,
    )
    return subs


class TestParseKaraoke:
    def test_splits_into_cumulative_centisecond_segments(self):
        segs = bs.parse_karaoke(r"{\k30}あ{\k20}い{\k50}う")
        assert segs == [(0, 30, "あ"), (30, 50, "い"), (50, 100, "う")]

    def test_strips_line_break_tags(self):
        segs = bs.parse_karaoke(r"{\k10}あ\Nい")
        assert segs == [(0, 10, "あい")]

    def test_returns_empty_without_k_tags(self):
        assert bs.parse_karaoke("プレーンテキスト") == []


class TestReadImagesWithDurations:
    def test_reads_concat_entries(self, tmp_path):
        inputs = tmp_path / "inputs.txt"
        inputs.write_text(
            "file 'cut_01.png'\nduration 40.000\n\nfile 'cut_02.png'\nduration 12.5\n",
            encoding="utf-8",
        )
        assert bs.read_images_with_durations(str(inputs)) == [("cut_01.png", 40.0), ("cut_02.png", 12.5)]

    def test_ignores_trailing_file_without_duration(self, tmp_path):
        inputs = tmp_path / "inputs.txt"
        inputs.write_text("file 'a.png'\nduration 1.0\nfile 'a.png'\n", encoding="utf-8")
        assert bs.read_images_with_durations(str(inputs)) == [("a.png", 1.0)]


class TestTimeline:
    def test_build_timeline_accumulates_offsets(self):
        timeline, total = bs.build_timeline("/w", [("a.png", 2.0), ("b.png", 3.0)])
        assert total == 5.0
        assert timeline == [(0.0, 2.0, "/w/a.png"), (2.0, 5.0, "/w/b.png")]

    def test_img_at_picks_active_image_and_clamps_to_last(self):
        timeline, _ = bs.build_timeline("/w", [("a.png", 2.0), ("b.png", 3.0)])
        assert bs.img_at(timeline, 0.0) == "/w/a.png"
        assert bs.img_at(timeline, 2.0) == "/w/b.png"
        assert bs.img_at(timeline, 99.0) == "/w/b.png"

    def test_event_at_returns_none_in_gaps(self):
        events = [(1.0, 2.0, "one"), (3.0, 4.0, "two")]
        assert bs.event_at(events, 1.5) == (1.0, 2.0, "one")
        assert bs.event_at(events, 2.5) is None
        assert bs.event_at(events, 4.0) is None


class TestCollectTransitionTimes:
    def test_karaoke_mode_includes_char_transitions(self):
        events = [(1.0, 2.0, r"{\k50}あ{\k50}い")]
        times = bs.collect_transition_times(events, 3.0)
        assert times == [0.0, 1.0, 1.5, 2.0, 3.0]

    def test_line_mode_only_uses_event_boundaries(self):
        events = [(1.0, 2.0, r"{\k50}あ{\k50}い")]
        times = bs.collect_transition_times(events, 3.0, include_char_transitions=False)
        assert times == [0.0, 1.0, 2.0, 3.0]


class TestSafeExtract:
    def test_rejects_path_traversal(self, tmp_path):
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("../escaped.txt", "x")
        target = tmp_path / "out"
        target.mkdir()
        with zipfile.ZipFile(zip_path) as z, pytest.raises(ValueError, match="path traversal"):
            bs.safe_extract(z, str(target))
        assert not (tmp_path / "escaped.txt").exists()

    def test_extracts_normal_members(self, tmp_path):
        zip_path = tmp_path / "ok.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("inputs.txt", "file 'a.png'\n")
        target = tmp_path / "out"
        target.mkdir()
        with zipfile.ZipFile(zip_path) as z:
            bs.safe_extract(z, str(target))
        assert (target / "inputs.txt").exists()


class TestColors:
    def test_hex_to_rgb(self):
        assert bs.hex_to_rgb("#FF8000") == (255, 128, 0)

    def test_color_to_rgb_drops_alpha(self):
        assert bs.color_to_rgb(pysubs2.Color(1, 2, 3, 128)) == (1, 2, 3)


class TestLoadStyleFile:
    def _write(self, tmp_path, data):
        path = tmp_path / "style.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_keeps_valid_values(self, tmp_path):
        style = bs.load_style_file(self._write(tmp_path, {"mode": "line", "position": "top", "font_size": 88}))
        assert style == {"mode": "line", "position": "top", "font_size": 88}

    def test_drops_unknown_mode_and_position(self, tmp_path, capsys):
        style = bs.load_style_file(self._write(tmp_path, {"mode": "bogus", "position": "middle"}))
        assert "mode" not in style and "position" not in style
        assert "Warning" in capsys.readouterr().err

    def test_drops_malformed_colors(self, tmp_path):
        style = bs.load_style_file(
            self._write(tmp_path, {"primary_color": "red", "outline_color": "#00FF00", "secondary_color": 123})
        )
        assert style == {"outline_color": "#00FF00"}

    def test_drops_missing_font(self, tmp_path):
        style = bs.load_style_file(self._write(tmp_path, {"font": "/no/such/font.ttf"}))
        assert "font" not in style

    def test_rejects_non_object_json(self, tmp_path):
        with pytest.raises(SystemExit):
            bs.load_style_file(self._write(tmp_path, ["not", "a", "dict"]))


class TestPickAssStyle:
    def test_prefers_karaoke_style(self):
        subs = make_subs()
        subs.styles["Other"] = pysubs2.SSAStyle(fontsize=1)
        assert bs.pick_ass_style(subs).fontsize == 64

    def test_falls_back_to_first_style(self):
        subs = make_subs(style_name="Default")
        assert bs.pick_ass_style(subs).fontsize == 64

    def test_errors_without_any_style(self):
        subs = pysubs2.SSAFile()
        subs.styles.clear()
        with pytest.raises(SystemExit):
            bs.pick_ass_style(subs, "x.ass")


class TestBuildRenderConfig:
    def test_scales_px_values_from_play_res_y(self, capsys):
        cfg = bs.build_render_config(make_subs(fontsize=64, play_res_y=1080), (1280, 2160), {})
        capsys.readouterr()
        # 実解像度 2160 / PlayResY 1080 = 2倍
        assert cfg.font.size == 128
        assert cfg.margin_v == 160
        assert cfg.outline == 6
        assert cfg.mode == "karaoke"

    def test_style_overrides_are_also_scaled(self, capsys):
        style_over = {
            "mode": "line",
            "position": "center",
            "font_size": 100,
            "margin_v": 50,
            "outline": 4,
            "letter_spacing": 6,
            "primary_color": "#112233",
            "secondary_color": "#445566",
            "outline_color": "#778899",
            "box": {"color": "#000000", "alpha": 0.5, "pad": 20},
        }
        cfg = bs.build_render_config(make_subs(play_res_y=1080), (1080, 540), style_over)
        capsys.readouterr()
        assert cfg.font.size == 50  # 100 * 0.5
        assert (cfg.margin_v, cfg.outline, cfg.letter_spacing) == (25, 2, 3)
        assert cfg.primary == (0x11, 0x22, 0x33)
        assert cfg.secondary == (0x44, 0x55, 0x66)
        assert cfg.outline_color == (0x77, 0x88, 0x99)
        assert cfg.box == ((0, 0, 0, 127), 10)
        assert (cfg.mode, cfg.position) == ("line", "center")

    def test_force_mode_wins_over_style_file(self, capsys):
        cfg = bs.build_render_config(make_subs(), (720, 1280), {"mode": "karaoke"}, force_mode="line")
        capsys.readouterr()
        assert cfg.mode == "line"

    def test_invalid_box_is_ignored(self, capsys):
        cfg = bs.build_render_config(make_subs(), (720, 1280), {"box": "yes"})
        capsys.readouterr()
        assert cfg.box is None


class TestLayout:
    """フォント実測に依存するレイアウト計算（システムフォントが必要）。"""

    @pytest.fixture
    def draw(self):
        return ImageDraw.Draw(Image.new("RGB", (10, 10)))

    @pytest.fixture
    def font(self):
        font = bs.load_font(40)
        if not hasattr(font, "font_variant") or getattr(font, "size", 0) != 40:
            pytest.skip("スケーラブルなフォントが見つからない環境ではスキップ")
        return font

    def test_measure_line_adds_letter_spacing(self, draw, font):
        plain = bs.measure_line(draw, "abcd", font, 0)
        spaced = bs.measure_line(draw, "abcd", font, 10)
        assert spaced == pytest.approx(plain + 30)

    def test_best_split_uses_space_boundaries_when_present(self, draw, font):
        assert bs.best_split(draw, "hello world", font, 0) == ("hello", "world")

    def test_best_split_falls_back_to_char_boundaries(self, draw, font):
        l1, l2 = bs.best_split(draw, "あいうえおかきくけこ", font, 0)
        assert l1 + l2 == "あいうえおかきくけこ"
        assert l1 and l2

    def test_best_split_returns_empty_second_line_for_single_char(self, draw, font):
        assert bs.best_split(draw, "あ", font, 0) == ("あ", "")

    def test_shrink_to_fit_reduces_size_until_it_fits(self, draw, font):
        fitted, size = bs.shrink_to_fit(draw, ["あいうえお"], font, 0, 100)
        assert size < 40
        assert fitted.size == size
        assert bs.measure_line(draw, "あいうえお", fitted, 0) <= 100

    def test_shrink_to_fit_stops_at_minimum_size(self, draw, font):
        fitted, size = bs.shrink_to_fit(draw, ["あ" * 60], font, 0, 100)
        assert size == bs.MIN_FONT_SIZE
        assert fitted.size == size

    def test_fit_line_layout_keeps_one_line_when_it_fits(self, draw, font):
        cfg = bs.RenderConfig(
            font=font, img_size=(1920, 1080), margin_v=10, outline=1,
            primary=(255, 255, 255), secondary=(0, 0, 0), outline_color=(0, 0, 0),
        )
        fitted, lines = bs.fit_line_layout(draw, "短い歌詞", cfg)
        assert lines == ["短い歌詞"]
        assert fitted.size == 40

    def test_fit_line_layout_wraps_very_long_text(self, draw, font):
        cfg = bs.RenderConfig(
            font=font, img_size=(300, 300), margin_v=10, outline=1,
            primary=(255, 255, 255), secondary=(0, 0, 0), outline_color=(0, 0, 0),
        )
        _, lines = bs.fit_line_layout(draw, "あ" * 40, cfg)
        assert len(lines) == 2
        assert "".join(lines) == "あ" * 40

    def test_fit_karaoke_layout_splits_on_segment_boundaries(self, draw, font):
        cfg = bs.RenderConfig(
            font=font, img_size=(300, 300), margin_v=10, outline=1,
            primary=(255, 255, 255), secondary=(0, 0, 0), outline_color=(0, 0, 0),
        )
        segs = bs.parse_karaoke("".join(rf"{{\k10}}{c}" for c in "あ" * 40))
        _, seg_lines = bs.fit_karaoke_layout(draw, segs, cfg)
        assert len(seg_lines) == 2
        assert sum(len(line) for line in seg_lines) == len(segs)
