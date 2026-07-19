# ✨ Lyric Video Maker

[![Language](https://img.shields.io/badge/Language-Python-blue)](https://www.python.org/)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen)](#)

## 🎯 概要

**Lyric Video Maker** は、MP3 音声ファイルとキーフレーム ZIP ファイルから、**カラオケ字幕付き MP4 動画**を生成する Python スクリプト群です。

ワークフローは 3 段階に分かれています。

```
① align_subtitles.py
   audio.mp3 + keyframes.zip|subtitles.ass|lyrics.txt  →  カラオケタイミング付き ASS（\k タグ）

② burn_subs.py
   audio.mp3 + keyframes.zip (+ ASS)  →  output.mp4（カラオケ字幕付きスライドショー）

②' burn_subs_video.py
   video.mp4 + ASS  →  映像を保ったまま1行スタイル字幕を焼き込んだ MP4

③ make_cover.py（任意）
   output.mp4  →  カバー画像 2 枚（YouTube サムネイル用 16:9 / ショート用 9:16）
```

既にタイミング済みの ASS がある場合は ② だけ実行すれば OK です。
歌詞がプレーンテキスト（.txt）しかない場合も ① にそのまま渡せます。

---

## 💎 特徴と設計思想

### 🎤 Whisper による文字レベル自動アライメント（align_subtitles.py）

- [stable-ts](https://github.com/jianfch/stable-ts) を使って Whisper の推論結果を文字レベルに分解します。
- ZIP 内の ASS から歌詞テキストだけを抽出して Whisper にアライメントさせ、句読点・記号は直前の文字の `\k` に吸収させます。
- 出力は pysubs2 互換の ASS ファイルで、スタイル情報は元ファイルから引き継ぎます。
- **プレーンテキスト歌詞対応**: `.txt`（1行=1字幕行、空行は無視）を渡すと、既定の Karaoke スタイル（Arial 64px・黄色ハイライト・PlayRes 1920x1080）で ASS を自動合成してからアライメントします。**行の区切りがそのまま字幕の行割りになる**ため、歌唱単位で改行してください。
- **歌い出し対応**: 1行目のみ、元 ASS の開始時刻が Whisper の判定より **0〜3秒だけ早い**場合に限り、元 ASS の開始時刻を採用します。3秒を超える差がある場合や Whisper の方が早い場合は Whisper の判定を使用します。元 ASS の1行目 Start を歌い出しに合わせておくと正確に表示されます。
- **繰り返し歌唱対応**: AI 音源が同じ歌詞を繰り返す場合、各行の終了時刻を次行開始直前まで延長し、繰り返し中も現在の歌詞を表示し続けます。

### 🎤 ASS カラオケ字幕の完全再現（burn_subs.py）

- `\k` タグを解析し、文字単位でハイライト色（黄）と待機色（白）を切り替えます。
- 文字ごとの遷移タイミングをすべて算出し、状態が変わるフレームのみを描画します。

### 🎬 スタイルファイルによる MV 風描画（--style-file）

- `--style-file style.json` で描画スタイルを上書きできます。`styles/` にプリセット（`rock.json` / `minimal.json` / `ballad.json`）あり。
- `"mode": "line"` にするとカラオケハイライトなしの **1行表示**になり、配置（`position`: top/center/bottom）・字間（`letter_spacing`）・背景座布団（`box`）が使えます。描画状態が行単位になるためカラオケより高速です。
- line モードでは幅に収まらない行を**自動調整**します: まず1行のままフォント縮小を試み、縮小率が 70% を下回るほど長い行は**2行に折り返し**ます（英語はスペース位置、日本語は幅バランスの良い文字境界で分割）。
- px 値（`font_size` / `margin_v` / `outline` / `letter_spacing` / `box.pad`）は **PlayResY（1080）基準**で指定し、実解像度へ自動スケールされます。色は `#RRGGBB`。不正値は警告して ASS の値にフォールバックします。

```json
{
  "mode": "line",
  "font_size": 88,
  "position": "bottom",
  "margin_v": 70,
  "letter_spacing": 4,
  "primary_color": "#FFFFFF",
  "outline_color": "#CC0000",
  "outline": 6,
  "box": {"color": "#000000", "alpha": 0.35, "pad": 22},
  "font": "/path/to/font.ttf"
}
```

（`box` / `font` は任意。`mode` 省略時は従来どおりカラオケ描画で、色やサイズの上書きのみ適用されます）
- **フォント**: macOS 標準の**ヒラギノ角ゴシック W7**を最優先で使用します。見つからない場合は**源ノ角ゴシック**（Source Han Sans VF）にフォールバックします（SIL Open Font License、YouTube 商用利用可）。Source Han Sans VF のインストールは `brew install --cask font-source-han-sans-vf`。

### 🖼️ カバー画像の自動生成（make_cover.py）

- 完成した MP4 の最初のフレームを ffmpeg で抽出し、タイトルとアーティスト名を合成した画像を 2 枚出力します。
  - `cover_youtube.png`（1280x720）: YouTube サムネイル推奨サイズ
  - `cover_short.png`（1080x1920）: ぼかし+減光した背景の中央に 16:9 フレームを重ねたショート用
- タイトル省略時は **MP4 の親フォルダ名**を自動で使用します。アーティスト名の既定は `Digital Armor Style`。
- 白タイトル+ダークストローク+シャドウ、アーティスト名は金色。長いタイトルは幅に収まるまで自動縮小します。
- フォント探索は `burn_subs.py` の `load_font` をそのまま利用しているため、字幕と同じフォントで描画されます。

### ⚡ 差分描画による高速処理

- フレームを 1 枚ずつ生成するのではなく、**字幕状態が変化するタイミングだけ**描画します。
- 3 分の動画でも約 200 枚の差分フレームで済むため、数十秒で完了します。

### 📐 自動スケーリング

- ZIP 内の `subtitles.ass` の `PlayResY` と実際の画像解像度から、フォントサイズ・マージン・アウトラインを自動計算します。
- 解像度を変えても設定変更不要です。

### 🗂️ ZIP 完結型の入力設計

- キーフレーム画像・タイムライン・字幕がすべて ZIP に同梱されており、配布・管理が容易です。

---

## 🚀 クイックスタート

### 依存パッケージのインストール

```sh
pip install -r requirements.txt
```

> ffmpeg が別途必要です（エンコードに使用）。
> ```sh
> brew install ffmpeg   # macOS
> ```

---

### 実行（コピペ用）

**精度重視（Whisper アライメントあり）:**

```sh
# ① タイミング生成
python3 align_subtitles.py input/audio.mp3 input/keyframes.zip input/subtitles_aligned.ass

# ② 動画生成
python3 burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4 --subs input/subtitles_aligned.ass
```

**歌詞が .txt しかない場合:**

```sh
# ① タイミング生成（lyrics.txt は 1行=1字幕行）
python3 align_subtitles.py input/audio.mp3 input/lyrics.txt input/subtitles_aligned.ass

# ② 動画生成
python3 burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4 --subs input/subtitles_aligned.ass
```

**簡易版（ZIP 内の ASS タイミングをそのまま使う）:**

```sh
python3 burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4
```

**②' 完成済み動画（MV）にスタイル字幕を焼き込む:**

```sh
python3 burn_subs_video.py input/video.mp4 input/subtitles_aligned.ass output/video_with_lyrics.mp4 --style-file styles/rock.json
```

**③ カバー画像生成（任意）:**

```sh
python3 make_cover.py output/output.mp4 --title "曲名"
```

---

## ⚙️ 引数

### align_subtitles.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `audio.mp3` | ✅ | アライメント対象の音声ファイル |
| `keyframes.zip` / `subtitles.ass` / `lyrics.txt` | ✅ | ZIP を渡すと内部の `subtitles.ass` を自動抽出。ASS ファイル直接、またはプレーンテキスト歌詞（1行=1字幕行）も可 |
| `output.ass` | ➖ | 出力 ASS ファイル名（省略時: `subtitles_aligned.ass`）|
| `--model` | ➖ | Whisper モデルサイズ（`base` / `small` / `medium` / `large-v3`、省略時: `large-v3`）|
| `--language` | ➖ | Whisper に渡す歌詞の言語コード（省略時: `ja`）|

**注意:** 入力 ASS の歌詞行数と Whisper が検出した文字数が一致しない場合はエラーで停止します。その場合は歌詞テキストの表記（スペース・句読点）を Whisper の出力に合わせて調整してください。

### burn_subs.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `audio.mp3` | ✅ | BGM として使用する音声ファイル |
| `keyframes.zip` | ✅ | PNG 画像・`inputs.txt`・`subtitles.ass` を含む ZIP |
| `output.mp4` | ➖ | 出力ファイル名（省略時: `output.mp4`）|
| `--subs <subtitles.ass>` | ➖ | ZIP 内の字幕を上書きする ASS ファイル |
| `--style-file <style.json>` | ➖ | 描画スタイルを上書きする JSON（`styles/` にプリセットあり）|

### burn_subs_video.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `video.mp4` | ✅ | 入力動画（映像はそのまま保持される） |
| `subtitles.ass` | ✅ | タイミング付き ASS（`\k` タグは無視し行の開始/終了のみ使用） |
| `output.mp4` | ➖ | 出力ファイル名（省略時: `output_video.mp4`）|
| `--style-file <style.json>` | ➖ | 描画スタイル JSON（line モード専用。`mode: "karaoke"` は無視）|

行ごとの字幕を透過 PNG に描画し、ffmpeg の `overlay` + `enable='between(t,...)'` で時間指定合成します。音声は再エンコードせずコピーします。

### make_cover.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `video.mp4` | ✅ | 入力 MP4（最初のフレームを使用） |
| `--title` | ➖ | タイトル（省略時: MP4 の親フォルダ名）|
| `--artist` | ➖ | アーティスト名（省略時: `Digital Armor Style`）|
| `--outdir` | ➖ | 出力フォルダ（省略時: MP4 と同じフォルダ）|

---

## 📦 ZIP ファイルの構成

```
keyframes.zip
├── cut_01.png       # キーフレーム画像
├── cut_02.png
├── ...
├── inputs.txt       # 画像ファイル名と表示尺の定義
└── subtitles.ass    # ASS カラオケ字幕
```

**inputs.txt の形式:**

```
file 'cut_01.png'
duration 40.000

file 'cut_02.png'
duration 50.000
```

---

## 🎨 字幕レンダリングの仕組み

1. `subtitles.ass` から `\k` タグを解析してカラオケ遷移タイミングをリストアップ
2. タイミングの変わり目ごとに PIL で画像にテキストを描画（アウトライン付き）
3. 描画済みフレームを ffmpeg の concat demuxer でつなぎ合わせ、音声と合成してエンコード

---

## 🤝 依存関係 (Dependencies)

| パッケージ | 用途 |
| --- | --- |
| [stable-ts](https://github.com/jianfch/stable-ts) | Whisper による文字レベル音声アライメント |
| [Pillow](https://pillow.readthedocs.io/) | PNG 画像への字幕描画 |
| [pysubs2](https://pysubs2.readthedocs.io/) | ASS 字幕ファイルのパース・スタイル取得 |
| [ffmpeg](https://ffmpeg.org/) | 動画エンコード・音声合成（システムインストール） |

---

## 📜 ライセンス (License)

このプロジェクトは [MIT License](https://opensource.org/licenses/MIT) の下で公開されています。
