"""Tests for make_cover.py's pure helpers and cover layouts (no ffmpeg: the frame is made in memory)."""

import argparse
import os
import sys

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import make_cover as mc  # noqa: E402


class TestParseHexColor:
    @pytest.mark.parametrize("text", ["8FE3FF", "#8FE3FF", "#8fe3ff"])
    def test_accepts_with_or_without_hash(self, text):
        assert mc.parse_hex_color(text) == (0x8F, 0xE3, 0xFF)

    @pytest.mark.parametrize("text", ["FFF", "#12345", "GGGGGG", ""])
    def test_rejects_anything_but_six_hex_digits(self, text):
        with pytest.raises(argparse.ArgumentTypeError):
            mc.parse_hex_color(text)


class TestCenterCropToRatio:
    def test_wide_image_loses_its_sides(self):
        img = Image.new("RGB", (400, 100))
        assert mc.center_crop_to_ratio(img, 16, 9).size == (178, 100)

    def test_tall_image_loses_top_and_bottom(self):
        img = Image.new("RGB", (160, 400))
        assert mc.center_crop_to_ratio(img, 16, 9).size == (160, 90)

    def test_matching_ratio_is_left_alone(self):
        img = Image.new("RGB", (320, 180))
        assert mc.center_crop_to_ratio(img, 16, 9).size == (320, 180)

    def test_crop_is_taken_from_the_centre(self):
        img = Image.new("RGB", (300, 100), (0, 0, 0))
        ImageDraw.Draw(img).rectangle([100, 0, 199, 99], fill=(255, 0, 0))
        cropped = mc.center_crop_to_ratio(img, 1, 1)
        assert cropped.size == (100, 100)
        assert cropped.getcolors() == [(100 * 100, (255, 0, 0))]


class TestFitFont:
    @pytest.fixture
    def draw(self):
        if getattr(mc.load_font(40), "size", 0) != 40:
            pytest.skip("skipped where no scalable font is available")
        return ImageDraw.Draw(Image.new("RGB", (10, 10)))

    def test_short_title_keeps_the_base_size(self, draw):
        _, size = mc.fit_font(draw, "短い", 40, 1000)
        assert size == 40

    def test_long_title_shrinks_to_fit(self, draw):
        font, size = mc.fit_font(draw, "あ" * 20, 40, 300)
        assert 12 < size < 40
        assert draw.textlength("あ" * 20, font=font) <= 300

    def test_never_goes_below_twelve(self, draw):
        _, size = mc.fit_font(draw, "あ" * 500, 40, 100)
        assert size == 12


class TestCovers:
    """Both covers from one in-memory 16:9 frame."""

    @pytest.fixture
    def frame(self):
        return Image.new("RGB", (640, 360), (0, 80, 160))

    def test_youtube_cover_is_1280x720_with_the_title_drawn(self, frame):
        cover = mc.make_youtube_cover(frame, "テスト曲", "Artist")
        assert cover.size == (1280, 720) and cover.mode == "RGB"
        colors = {c for _, c in cover.getcolors(maxcolors=1 << 22)}
        assert mc.TITLE_COLOR in colors and mc.ARTIST_COLOR in colors

    def test_short_cover_is_1080x1920_with_the_frame_above_the_title(self, frame):
        cover = mc.make_short_cover(frame, "テスト曲", "Artist", artist_color=(0x8F, 0xE3, 0xFF))
        assert cover.size == (1080, 1920)
        # the sharp 16:9 frame sits around 42 % of the height; the title is below it
        assert cover.getpixel((540, round(1920 * 0.42))) == (0, 80, 160)
        lower = cover.crop((0, 1200, 1080, 1920))
        colors = {c for _, c in lower.getcolors(maxcolors=1 << 22)}
        assert mc.TITLE_COLOR in colors and (0x8F, 0xE3, 0xFF) in colors
