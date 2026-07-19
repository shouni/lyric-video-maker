# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Makefile やテストスイートは無い。フラットな Python スクリプト3本のリポジトリで、実行がそのまま動作確認になる。

依存は `.venv`（Python 3.9）にインストール済み。**必ず `.venv/bin/python` で実行する**こと。システム python3 には pysubs2 / stable-ts が無い。ffmpeg はシステムインストール（brew）。

```bash
# ① 歌詞アライメント: 音声 + 歌詞 → カラオケタイミング付き ASS
.venv/bin/python align_subtitles.py input/audio.mp3 input/keyframes.zip input/subtitles_aligned.ass
# 歌詞入力は keyframes.zip / .ass / .txt（1行=1字幕行）のいずれでも可

# ② 字幕焼き込み: 音声 + keyframes.zip (+ ASS) → MP4
.venv/bin/python burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4 --subs input/subtitles_aligned.ass

# ②' 完成済み動画に1行スタイル字幕を焼き込み（映像保持、line モード専用）
.venv/bin/python burn_subs_video.py input/video.mp4 subtitles_aligned.ass out.mp4 --style-file styles/rock.json

# ③ カバー画像: MP4 の最初のフレーム → cover_youtube.png (1280x720) + cover_short.png (1080x1920)
.venv/bin/python make_cover.py output/output.mp4 --title "曲名"
```

動作確認を速くしたいときは `--model small` を使う（既定は `large-v3`。モデルは `~/.cache/whisper/` にキャッシュ済み）。`input/` にサンプル一式（mp3 + keyframes.zip + aligned ASS）があり、アライメントのテストに使える。

## Architecture

AP MV エコシステム（keyframes.zip を生成する側）のローカル後処理ツール群。①→②→③ のパイプラインで、各段の受け渡しはファイル。

**姉妹リポジトリとの関係**: `~/PycharmProjects/lyric-video` は本リポジトリを Cloud Run Web サービス化したもので、`app/align_subtitles.py` / `app/burn_subs.py` は本リポジトリのほぼコピー（差分は Docker 用 Linux フォントパス程度）。片方を修正したらもう片方への移植が必要か検討すること。

### align_subtitles.py — 強制アライメント（文字起こしではない）

stable-ts の `model.align()` で**既存の歌詞テキストを音声のタイミングに割り当てる**。ゼロからの transcribe パスは無い。

- 歌詞入力は3形式: keyframes.zip 内の `subtitles.ass` / 単体 ASS / プレーンテキスト。`.txt` の場合は `subs_from_txt()` が既定の Karaoke スタイル（Arial 64px・黄ハイライト・PlayRes 1920x1080、既存 subtitles.ass と同一値）で ASS を合成してから同じフローに乗せる。行の区切りがそのまま字幕の行割りになる。
- **文字数照合が厳密**: 句読点・記号（`PUNCT_PATTERN`）を除いた歌詞の文字数と Whisper の検出文字数が一致しないと中断する。歌詞と歌唱のズレ（アドリブ・繰り返し省略）で失敗する設計。
- 句読点は直前の文字の `\k` に時間ごと吸収させる。1行目のみ、元 ASS の開始が Whisper 判定より 0〜3 秒早い場合は元 ASS の開始時刻を採用（歌い出し対応）。行間ギャップは次行開始直前まで延長（繰り返し歌唱対応）。

### burn_subs.py — 差分描画によるカラオケ焼き込み

`\k` タグを解析し、**字幕状態が変化する時刻だけ**フレームを描画して ffmpeg concat で結合する。状態キーは（背景画像, 字幕イベント, ハイライト済み文字数）で、同一状態のフレームはキャッシュ再利用。フォントサイズ・マージン・アウトラインは ASS の `PlayResY` と実画像解像度の比率で自動スケールする。スタイルは `Karaoke` という名前のスタイルを優先して読む。

`--style-file`（JSON、`styles/` にプリセット）で描画スタイルを上書きできる。`mode: "line"` はカラオケハイライトなしの1行表示で、文字単位の遷移を集めないため状態数が大幅に減る（サンプルで 223→33）。px 値はすべて PlayResY 基準で指定し実解像度へスケールする、という既存の規約に従うこと。不正値は警告してフォールバックする設計（動画生成自体は失敗させない）。

line モードの `render_line_frame` は幅に収まらない行を自動調整する（`fit_line_layout`: 縮小率 `LINE_SHRINK_FLOOR` までは1行のまま縮小、それ以下なら `best_split` で2行折り返し。英語はスペース位置のみ、日本語は全文字境界から幅バランス最良点を選ぶ）。透過 RGBA レイヤーを渡すと透過を保って返す（burn_subs_video.py のオーバーレイ用。box 描画時にモードを維持する実装に依存している）。

### burn_subs_video.py — 完成済み動画への字幕焼き込み

スライドショーではなく実写 MV 動画（映像・カメラワーク保持）に字幕を乗せる。行ごとに `render_line_frame` で透過 PNG を作り、ffmpeg の `overlay` + `enable='between(t,start,end)'` チェーンで時間指定合成する（音声はコピー）。行の重なりは `build_windows` が「先の行優先」（burn_subs の `event_at` と同じ規則）で解消する。line モード専用（カラオケの文字単位状態をオーバーレイにすると数百入力になるため非対応）。

**注意**: 出力 MP4 は「1フレームが数秒続く」特殊構造（キーフレームも先頭のみ）のため、`ffmpeg -ss` でのフレーム取り出しは別のフレームを拾って「字幕が無い」ように見えることがある。検証には `-vf "select=eq(n\,N)" -vsync 0` でフレーム番号指定を使うこと。

フォントは `FONT_CANDIDATES` の順に探索（ヒラギノ角ゴ W7 が最優先）。`load_font()` は make_cover.py からも import されており、字幕とカバーのフォントを一致させている。

### make_cover.py — カバー画像生成

MP4 の最初のフレームを ffmpeg で抽出し、Pillow でタイトル＋アーティスト名を合成。タイトル省略時は **MP4 の親フォルダ名**を使う（曲名フォルダに動画を置く運用が前提）。アーティスト名の既定は `Digital Armor Style`。長いタイトルは幅 92% に収まるまでフォント自動縮小。ショート版はぼかし+減光した背景の中央に 16:9 フレームを重ねる。

`from burn_subs import load_font` している関係で pysubs2 まで transitively 必要になるため、これも `.venv` 経由での実行が必須。

### keyframes.zip の構成

`cut_XX.png`（キーフレーム画像）+ `inputs.txt`（ffmpeg concat 形式の画像名と表示秒数）+ `subtitles.ass`。詳細は README.md 参照。
