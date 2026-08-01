#!/usr/bin/env python3
"""音声からカラオケタイミングを取得し、ASS字幕を再生成する。

Usage:
    python3 align_subtitles.py <audio.mp3> <keyframes.zip|subtitles.ass|lyrics.txt> [output.ass]

歌詞入力は keyframes.zip 内の subtitles.ass、単体の ASS ファイル、
またはプレーンテキスト（1行=1字幕行、空行は無視）を受け付ける。
"""

import os
import re
import sys
import zipfile
import argparse
import stable_whisper
import pysubs2

PUNCT_PATTERN = re.compile(r'[　 、。！？!?,.\s\-\[\]\(\)「」『』〜♪…※☆★●○◎]')
TAG_PATTERN = re.compile(r'\{[^}]*\}')
LINE_BREAK_PATTERN = re.compile(r'\\[nNh]')
TAIL_MS = 300  # 最終文字後の表示延長
FILL_GAP_MARGIN_MS = 100  # 繰り返し歌唱時に次行との間に残す余白
# 1行目のみ、元ASSの開始がWhisper判定よりこの範囲だけ早い場合は元ASSを採用（歌い出し対応）
LEAD_IN_TOLERANCE_MS = 3000
DEFAULT_K_CS = 10  # タイミングが尽きた文字に割り当てる既定の表示長


def words_to_chars(segments):
    """Convert Whisper word timestamps into evenly distributed character timings."""
    chars = []
    for seg in segments:
        if not (hasattr(seg, 'words') and seg.words):
            continue
        for w in seg.words:
            word = w.word.strip()
            if not word:
                continue
            n = len(word)
            per = (w.end - w.start) / n
            for i, ch in enumerate(word):
                chars.append({
                    "char": ch,
                    "start": w.start + i * per,
                    "end":   w.start + (i + 1) * per,
                })
    return chars


def ms(sec):
    """Convert seconds to integer milliseconds for ASS subtitle events."""
    return int(sec * 1000)


def subs_from_txt(path):
    """プレーンテキスト歌詞（1行=1字幕行）から ASS を合成する。

    スタイルは既存の subtitles.ass と同じ Karaoke スタイル
    （Arial 64px、黄色ハイライト、PlayRes 1920x1080）で作成し、
    burn_subs.py がそのまま読める形にする。
    イベントの時刻は仮置き（アライメントで上書きされる）。
    """
    with open(path, encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    if not lines:
        raise SystemExit(f"Error: 歌詞テキストが空です: {path}")

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = "1920"
    subs.info["PlayResY"] = "1080"
    subs.styles["Karaoke"] = pysubs2.SSAStyle(
        fontname="Arial",
        fontsize=64,
        primarycolor=pysubs2.Color(255, 255, 0),    # &H0000FFFF 黄
        secondarycolor=pysubs2.Color(255, 255, 255),  # &H00FFFFFF 白
        outlinecolor=pysubs2.Color(0, 0, 0),
        backcolor=pysubs2.Color(0, 0, 0, 128),
        bold=True,
        outline=3,
        shadow=1,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,
        marginl=10,
        marginr=10,
        marginv=80,
    )
    for i, line in enumerate(lines):
        subs.append(pysubs2.SSAEvent(
            start=i * 5000,
            end=(i + 1) * 5000,
            style="Karaoke",
            text=line,
        ))
    return subs


def plain_text(text):
    """ASSイベントのテキストから装飾タグと改行タグを取り除く。"""
    return LINE_BREAK_PATTERN.sub('', TAG_PATTERN.sub('', text)).strip()


def load_source_subs(path):
    """歌詞入力（keyframes.zip / ASS / プレーンテキスト）を読み込んで SSAFile を返す。"""
    if not os.path.exists(path):
        raise SystemExit(f"Error: 歌詞入力が見つかりません: {path}")
    if path.endswith(".txt"):
        return subs_from_txt(path)
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "subtitles.ass" not in names:
                raise SystemExit(
                    f"Error: keyframes.zip に subtitles.ass が含まれていません。 ZIP内のファイル: {names}"
                )
            with zf.open("subtitles.ass") as f:
                return pysubs2.SSAFile.from_string(f.read().decode("utf-8"))
    return pysubs2.load(path)


def extract_lyric_lines(subs):
    """テキストを持つイベントの歌詞行だけを順番に取り出す。"""
    return [plain for plain in (plain_text(e.text) for e in subs) if plain]


def map_chars_to_lines(lyric_lines, whisper_chars):
    """歌詞の各行に、Whisperが検出した文字タイミングを割り当てる。

    句読点・記号（PUNCT_PATTERN）を除いた文字数が一致しない場合は、行全体の
    タイミングがずれるため ValueError を送出して中断する（歌詞と歌唱のズレ検出）。
    """
    flat_orig = [
        {"line": line_idx, "char": ch}
        for line_idx, line in enumerate(lyric_lines)
        for ch in line
        if not PUNCT_PATTERN.match(ch)
    ]
    flat_whisper = [c for c in whisper_chars if PUNCT_PATTERN.match(c["char"]) is None]

    if len(flat_orig) != len(flat_whisper):
        raise ValueError(
            f"文字数が一致しません (orig={len(flat_orig)}, whisper={len(flat_whisper)})。"
            "タイミングが全体的にずれるためアライメントを中断します。"
        )

    line_char_map = {}
    for orig, timing in zip(flat_orig, flat_whisper):
        line_char_map.setdefault(orig["line"], []).append(timing)
    return line_char_map


def build_karaoke_text(plain, char_timings):
    """歌詞1行と文字タイミングから \\k タグ付きのASSテキストを組み立てる。

    句読点は直前の文字の \\k に吸収させる（句読点自体には歌唱時間が無いため）。
    """
    k_parts = []
    ti = 0
    for ch in plain:
        if PUNCT_PATTERN.match(ch):
            if k_parts:
                k_parts[-1]["text"] += ch
            else:
                k_parts.append({"k_cs": 0, "text": ch})
        elif ti < len(char_timings):
            t = char_timings[ti]
            k_parts.append({"k_cs": max(1, round((t["end"] - t["start"]) * 100)), "text": ch})
            ti += 1
        else:
            k_parts.append({"k_cs": DEFAULT_K_CS, "text": ch})
    return "".join(f"{{\\k{p['k_cs']}}}{p['text']}" for p in k_parts)


def fill_repeat_gaps(subs):
    """行間のギャップを次行の開始直前まで詰める（繰り返し歌唱中も歌詞を出し続ける）。"""
    text_events = [e for e in subs if plain_text(e.text)]
    for curr, next_ev in zip(text_events, text_events[1:]):
        if next_ev.start - curr.end > FILL_GAP_MARGIN_MS:
            curr.end = next_ev.start - FILL_GAP_MARGIN_MS


def build_aligned_subs(subs_orig, line_char_map, verbose=True):
    """元のASSと行ごとの文字タイミングから、\\kタグ付きの新しいASSを生成する。"""
    new_subs = pysubs2.SSAFile()
    new_subs.info = subs_orig.info.copy()
    new_subs.styles = subs_orig.styles.copy()

    valid_line_idx = 0
    for event in subs_orig:
        plain = plain_text(event.text)
        if not plain:
            # テキストを持たないイベント（空行やタグのみ）はそのまま保持
            new_subs.append(event.copy())
            continue

        char_timings = line_char_map.get(valid_line_idx)
        valid_line_idx += 1
        if not char_timings:
            new_subs.append(event.copy())
            continue

        line_start_s = char_timings[0]["start"]
        line_end_s = char_timings[-1]["end"] + TAIL_MS / 1000

        new_event = event.copy()
        # 最初の行は歌い出し対応のため、元のASSがWhisperより少し早い場合のみ元のASSを採用
        if valid_line_idx == 1 and 0 < ms(line_start_s) - event.start <= LEAD_IN_TOLERANCE_MS:
            new_event.start = event.start
        else:
            new_event.start = ms(line_start_s)
        new_event.end = ms(line_end_s)
        new_event.text = build_karaoke_text(plain, char_timings)
        new_subs.append(new_event)

        if verbose:
            print(f"  行{valid_line_idx}: {line_start_s:.2f}s - {line_end_s:.2f}s | {plain}")

    fill_repeat_gaps(new_subs)
    return new_subs


def main():
    """Align existing subtitle text to audio and write a new karaoke-timed ASS file."""
    parser = argparse.ArgumentParser(description="音声からカラオケタイミングを取得し、ASS字幕を再生成する。")
    parser.add_argument("audio", help="Input audio file (mp3)")
    parser.add_argument("subtitles_in", help="Input keyframes ZIP, subtitles ASS, or plain lyrics TXT file")
    parser.add_argument("subtitles_out", nargs="?", default="subtitles_aligned.ass", help="Output subtitles file (ass)")
    parser.add_argument("--model", default="large-v3", help="Whisper model size (e.g., base, small, medium, large-v3)")
    parser.add_argument("--language", default="ja", help="Lyrics language code passed to Whisper (e.g., ja, en)")
    args = parser.parse_args()

    audio = args.audio
    ass_out = args.subtitles_out

    if not os.path.exists(audio):
        raise SystemExit(f"Error: 音声ファイルが見つかりません: {audio}")

    # --- 元のASS読み込み（ZIP・ASS・プレーンテキスト歌詞を受け付ける）---
    subs_orig = load_source_subs(args.subtitles_in)
    lyric_lines = extract_lyric_lines(subs_orig)
    if not lyric_lines:
        raise SystemExit(f"Error: 歌詞行が1行もありません: {args.subtitles_in}")

    # --- アライメント実行 ---
    print("Whisperモデル読み込み中...")
    model = stable_whisper.load_model(args.model)

    print("アライメント実行中...")
    result = model.align(audio, "\n".join(lyric_lines), language=args.language)

    # adjust_by_silence は音楽トラックでは逆効果になるため一旦無効化
    # result = result.adjust_by_silence(audio, vad=True)

    # --- 文字レベルのタイムスタンプを収集（複数文字トークンは時間を等分配）---
    all_chars = words_to_chars(result.segments)
    print(f"取得文字数: {len(all_chars)}")

    # --- 元の字幕行の文字と照合（句読点・スペース・記号は除いてマッチング）---
    try:
        line_char_map = map_chars_to_lines(lyric_lines, all_chars)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"照合文字数: {sum(len(v) for v in line_char_map.values())}")

    new_subs = build_aligned_subs(subs_orig, line_char_map)
    new_subs.save(ass_out)
    print(f"\n完了: {ass_out}")


if __name__ == "__main__":
    main()
