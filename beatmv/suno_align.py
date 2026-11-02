"""Suno の時刻合わせと Whisper の時刻合わせを組み合わせて lyrics.ass を作る（lyrics_aligner = "hybrid"）。

カナで歌わせた曲は、どちらか一方だけでは字幕にならない:
- Suno の時刻合わせ（audio-lyrics-alignment）は行の位置が正確だが、文字はカナで返る。漢字の字幕に
  そのまま使えない。
- Whisper（align_subtitles.py）は漢字の歌詞に 1 文字ずつ時刻を振れるが、行の開始が前の行の終わりまで
  引き戻され、伸ばした語の後ろに数秒の空白を抱えることがある。Overclock（2026-11-02）では終盤の
  3 行が 2〜4 秒ずれた。

そこで行の開始と終わりは Suno から、行の中の文字の配分は Whisper から取る。Whisper の行の両端は
信用できないので、先に最初の文字を FIRST_SEG_CAP、最後の文字を LAST_SEG_CAP までに詰めてから、
Suno の行の範囲へ線形に引き伸ばす。

Suno の時刻合わせは MCP からしか取れないので、呼び出し側が JSON を work/<slug>/suno_alignment.json に
保存しておく（audio-lyrics-alignment の応答そのまま、または words の配列）。
"""
import json
import re

import pysubs2

import burn_subs

# Whisper の最初の文字は前の行の残りを抱えるので、この長さまで詰めてから引き伸ばす。
FIRST_SEG_CAP = 0.4
# 伸ばした最後の文字は間奏まで続くことがあるので、この長さまで詰める。
LAST_SEG_CAP = 1.0
# 合成した後にこれより長い文字が残る行は、伸ばしの配分を目で確かめる（Overclock では 2 行あった）。
HELD_WARN = 1.5
# 行の開始がこれ以上ずれる行は、どちらの時刻が正しいかを耳で確かめる（Overclock の終盤は 2〜4 秒）。
START_WARN = 2.0

_TAG = re.compile(r"\[[^\]]*\]")


def load_words(path):
    """保存した Suno の時刻合わせ（応答そのまま、または words の配列）から words を取り出す。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    words = data.get("words") if isinstance(data, dict) else data
    if not isinstance(words, list) or not words:
        raise ValueError(f"{path}: words が見つかりません")
    return words


def line_spans(words):
    """Suno の words を、歌った行ごとの (開始秒, 終了秒) に分ける。

    Suno は行の区切りを語の頭の改行で表し、セクションタグ（演出つきの [Verse: ...] など）も語の中に
    混ぜて返す。タグを除いた語が改行のあとに文字を持てば、そこから新しい行が始まる。改行のあとが
    空白だけなら（最後の語の後ろに [Outro] が続く場合など）、その語はまだ前の行に属する。
    """
    spans = []
    for w in words:
        text = _TAG.sub("", w["word"])
        start, end = float(w["start_s"]), float(w["end_s"])
        if "\n" in text and text.rsplit("\n", 1)[1].strip():
            spans.append([start, end])
        elif spans:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [tuple(s) for s in spans]


def _retime_line(segs, span):
    """Whisper の 1 行（[トークン, 開始, 終了] の列）を Suno の行の範囲へ写す。"""
    segs = [list(s) for s in segs]
    if segs[0][2] - segs[0][1] > FIRST_SEG_CAP:
        segs[0][1] = segs[0][2] - FIRST_SEG_CAP
    if segs[-1][2] - segs[-1][1] > LAST_SEG_CAP:
        segs[-1][2] = segs[-1][1] + LAST_SEG_CAP
    ws, we = segs[0][1], segs[-1][2]
    ss, se = span
    scale = (se - ss) / max(we - ws, 1e-3)
    bounds = [ss + (s[1] - ws) * scale for s in segs] + [se]
    return [(s[0], bounds[i], bounds[i + 1]) for i, s in enumerate(segs)]


def merge(whisper_ass, words):
    """Whisper の ASS を Suno の行の範囲で組み直した SSAFile と、確かめたほうがよい行の一覧を返す。

    行数が合わなければ中断する。Suno と Whisper のどちらかが行を落とした・足したということで、
    行の対応を推測で合わせると、別の行の時刻で塗ることになる。
    """
    subs = pysubs2.load(whisper_ass)
    spans = line_spans(words)
    if len(spans) != len(subs):
        raise ValueError(
            f"行数が合いません: Suno {len(spans)} 行 / Whisper {len(subs)} 行。"
            "どちらかが行を落としたか、歌詞ファイルと歌った内容が違います")

    warnings = []
    for n, (event, span) in enumerate(zip(subs, spans), 1):
        t0 = event.start / 1000
        segs = [(txt, t0 + a / 100, t0 + b / 100) for a, b, txt in burn_subs.parse_karaoke(event.text)]
        if not segs:
            continue
        timed = _retime_line(segs, span)
        cs = [round(timed[0][1] * 100)] + [round(e * 100) for _, _, e in timed]
        event.start, event.end = cs[0] * 10, cs[-1] * 10
        event.text = "".join(f"{{\\k{cs[i + 1] - cs[i]}}}{tok}" for i, (tok, _, _) in enumerate(timed))

        held = max(timed, key=lambda s: s[2] - s[1])
        if held[2] - held[1] > HELD_WARN:
            warnings.append(f"{n} 行目「{''.join(s[0] for s in timed)}」: 「{held[0]}」が "
                            f"{held[2] - held[1]:.1f} 秒あります。伸ばしの配分を確かめてください")
        whisper_start = max(segs[0][1], segs[0][2] - FIRST_SEG_CAP)
        if abs(whisper_start - span[0]) > START_WARN:
            warnings.append(f"{n} 行目: Whisper と Suno の行の開始が {START_WARN:.0f} 秒以上ずれています"
                            f"（Whisper {whisper_start:.1f} 秒 / Suno {span[0]:.1f} 秒）。"
                            "Suno の時刻を採っています")
    return subs, warnings
