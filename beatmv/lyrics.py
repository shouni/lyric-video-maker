"""\\k 付き ASS の歌詞を、文字（トークン）単位の時刻に分ける。描画と SRT が同じ時刻を使う。

ASS の出どころは 2 通り（song.toml の lyrics_aligner）:
- suno: Suno の時刻合わせ（audio-lyrics-alignment）から作ったもの。行の開始は歌い出しそのもの。
- whisper: align_subtitles.py で分離ボーカルに歌詞を合わせたもの。カナで歌わせた曲は Suno の時刻合わせも
  カナで返るので、漢字の字幕にはこちらを使う。align_subtitles.py は行の開始を前の行の終わりまで
  繰り上げ、その分を最初の文字の \\k に入れる（「駆」が \\k156 など）。そのまま塗ると歌う前から色が
  付くので、最初の文字が FIRST_SEG_MAX より長いときは終わりの FIRST_SEG_WIPE 秒前から塗る。
  駆け抜けろ Midnight では、Suno の時刻合わせの歌い出しより 0.16〜0.29 秒早い範囲に収まった。
"""
import bisect
import math

import pysubs2

import burn_subs

FIRST_SEG_MAX = 0.5
FIRST_SEG_WIPE = 0.35


def load_lines(ass_path, aligner):
    """各行を (表示テキスト, 行の開始, 行の終わり, [(トークン, 開始秒, 終了秒), ...]) にする。"""
    out = []
    for e in pysubs2.load(ass_path):
        t0 = e.start / 1000
        segs = [(txt, t0 + a / 100, t0 + b / 100) for a, b, txt in burn_subs.parse_karaoke(e.text)]
        if not segs:
            continue
        if aligner == "whisper":
            txt, a, b = segs[0]
            if b - a > FIRST_SEG_MAX:
                segs[0] = (txt, b - FIRST_SEG_WIPE, b)
        out.append(("".join(x[0] for x in segs), t0, e.end / 1000, segs))
    return out


def current_line(lines, t, linger):
    """表示中の行番号。次の行が近ければその直前まで、遠ければ歌い終わりから linger 秒まで。"""
    cur = None
    for i, (_, s, e, _) in enumerate(lines):
        nxt = lines[i + 1][1] if i + 1 < len(lines) else math.inf
        end = max(min(nxt - 0.25, e + linger), e + 0.4)
        if s - 0.25 <= t < end:
            cur = i
    return cur


def progress(segs, t):
    """塗り終わったトークン数（小数）。各トークンはその \\k の長さをかけて塗る。"""
    k = bisect.bisect_right([a for _, a, _ in segs], t) - 1
    if k < 0:
        return 0.0
    _, a, b = segs[k]
    return k + min(1.0, (t - a) / max(b - a, 1e-3))


def to_srt(lines, out_path):
    """YouTube 字幕用の SRT。行は最初の文字の歌い出しから、次の行の歌い出しまで出す。"""
    subs = pysubs2.SSAFile()
    for i, (text, _, end, segs) in enumerate(lines):
        start = segs[0][1]
        if i + 1 < len(lines):
            end = min(end, lines[i + 1][3][0][1])
        subs.append(pysubs2.SSAEvent(start=round(start * 1000), end=round(end * 1000), text=text))
    subs.save(out_path, format_="srt")
    return subs
