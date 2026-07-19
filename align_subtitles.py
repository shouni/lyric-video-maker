#!/usr/bin/env python3
"""音声からカラオケタイミングを取得し、ASS字幕を再生成する。

Usage:
    python3 align_subtitles.py <audio.mp3> <keyframes.zip|subtitles.ass|lyrics.txt> [output.ass]

歌詞入力は keyframes.zip 内の subtitles.ass、単体の ASS ファイル、
またはプレーンテキスト（1行=1字幕行、空行は無視）を受け付ける。
"""

import re
import sys
import zipfile
import argparse
import stable_whisper
import pysubs2

PUNCT_PATTERN = re.compile(r'[　 、。！？!?,.\s\-\[\]\(\)「」『』〜♪…※☆★●○◎]')
TAIL_MS = 300  # 最終文字後の表示延長
FILL_GAP_MARGIN_MS = 100  # 繰り返し歌唱時に次行との間に残す余白


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

    # --- 元のASS読み込み（ZIP・ASS・プレーンテキスト歌詞を受け付ける）---
    if args.subtitles_in.endswith(".txt"):
        subs_orig = subs_from_txt(args.subtitles_in)
    elif args.subtitles_in.endswith(".zip"):
        with zipfile.ZipFile(args.subtitles_in) as zf:
            names = zf.namelist()
            if "subtitles.ass" not in names:
                raise SystemExit(
                    f"Error: keyframes.zip に subtitles.ass が含まれていません。"
                    f" ZIP内のファイル: {names}"
                )
            with zf.open("subtitles.ass") as f:
                subs_orig = pysubs2.SSAFile.from_string(f.read().decode("utf-8"))
    else:
        subs_orig = pysubs2.load(args.subtitles_in)
    orig_events = []
    for event in subs_orig:
        plain = re.sub(r'\{[^}]*\}', '', event.text)
        plain = re.sub(r'\\[nNh]', '', plain).strip()
        if plain:
            orig_events.append(plain)

    text_to_align = "\n".join(orig_events)

    # --- アライメント実行 ---
    print("Whisperモデル読み込み中...")
    model = stable_whisper.load_model(args.model)

    print("アライメント実行中...")
    result = model.align(audio, text_to_align, language=args.language)

    # adjust_by_silence は音楽トラックでは逆効果になるため一旦無効化
    # result = result.adjust_by_silence(audio, vad=True)

    # --- 文字レベルのタイムスタンプを収集 ---
    # 複数文字トークンは時間を等分配
    all_chars = words_to_chars(result.segments)
    print(f"取得文字数: {len(all_chars)}")

    # --- 元の字幕行の文字と照合 ---
    # 句読点・スペース・記号を除いてマッチング
    flat_orig = []
    for line_idx, line in enumerate(orig_events):
        for ch in line:
            if PUNCT_PATTERN.match(ch):
                continue
            flat_orig.append({"line": line_idx, "char": ch})

    flat_whisper = [c for c in all_chars if PUNCT_PATTERN.match(c["char"]) is None]

    if len(flat_orig) != len(flat_whisper):
        print(f"Error: 文字数が一致しません (orig={len(flat_orig)}, whisper={len(flat_whisper)})。タイミングが全体的にずれるためアライメントを中断します。", file=sys.stderr)
        sys.exit(1)

    n = len(flat_orig)
    print(f"照合文字数: {n}")

    # 各行の開始・終了インデックスを記録
    line_char_map = {}  # line_idx -> [char_timing, ...]
    for i in range(n):
        li = flat_orig[i]["line"]
        if li not in line_char_map:
            line_char_map[li] = []
        line_char_map[li].append(flat_whisper[i])

    # --- 新しいASSイベントを生成 ---
    new_subs = pysubs2.SSAFile()
    new_subs.info = subs_orig.info.copy()
    new_subs.styles = subs_orig.styles.copy()

    valid_line_idx = 0
    for event in subs_orig:
        plain = re.sub(r'\{[^}]*\}', '', event.text)
        plain = re.sub(r'\\[nNh]', '', plain).strip()

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
        line_end_s   = char_timings[-1]["end"] + TAIL_MS / 1000

        # 元の行の文字（句読点含む）に \k を割り当て
        # 句読点はその前の文字の \k に吸収させる
        timing_queue = list(char_timings)
        k_parts = []
        ti = 0

        for ch in plain:
            is_punct = bool(PUNCT_PATTERN.match(ch))
            if is_punct:
                # 句読点は直前の \k に時間を加算（または 0cs でスキップ）
                if k_parts:
                    k_parts[-1]["text"] += ch
                else:
                    k_parts.append({"k_cs": 0, "text": ch})
            else:
                if ti < len(timing_queue):
                    t = timing_queue[ti]
                    duration_s = t["end"] - t["start"]
                    k_cs = max(1, round(duration_s * 100))
                    k_parts.append({"k_cs": k_cs, "text": ch})
                    ti += 1
                else:
                    k_parts.append({"k_cs": 10, "text": ch})

        # \k タグ付きテキスト生成
        ass_text = "".join(f"{{\\k{p['k_cs']}}}{p['text']}" for p in k_parts)

        new_event = event.copy()
        # 最初の行は歌い出し対応のため、元のASSがWhisperより少し早い場合のみ元のASSを採用
        if valid_line_idx == 1 and 0 < ms(line_start_s) - event.start <= 3000:
            new_event.start = event.start
        else:
            new_event.start = ms(line_start_s)
        new_event.end   = ms(line_end_s)
        new_event.text  = ass_text
        new_subs.append(new_event)

        print(f"  行{valid_line_idx}: {line_start_s:.2f}s - {line_end_s:.2f}s | {plain}")

    # --- 繰り返し歌唱対応: ギャップを次行開始直前まで延長 ---
    text_events = [e for e in new_subs if re.sub(r'\{[^}]*\}', '', e.text).strip()]
    for i in range(len(text_events) - 1):
        curr = text_events[i]
        next_ev = text_events[i + 1]
        gap_ms = next_ev.start - curr.end
        if gap_ms > FILL_GAP_MARGIN_MS:
            curr.end = next_ev.start - FILL_GAP_MARGIN_MS

    new_subs.save(ass_out)
    print(f"\n完了: {ass_out}")


if __name__ == "__main__":
    main()
