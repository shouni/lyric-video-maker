# ✨ Lyric Video Maker

[![Language](https://img.shields.io/badge/Language-Python-blue)](https://www.python.org/)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen)](#)

## 🎯 概要

**Lyric Video Maker** は、MP3 音声ファイルとキーフレーム ZIP ファイルから、**カラオケ字幕付き MP4 動画**を生成する Python スクリプト群です。

ワークフローは 2 段階に分かれています。

```
① align_subtitles.py
   audio.mp3 + keyframes.zip  →  カラオケタイミング付き ASS（\k タグ）

② burn_subs.py
   audio.mp3 + keyframes.zip (+ ASS)  →  output.mp4（カラオケ字幕付き）
```

既にタイミング済みの ASS がある場合は ② だけ実行すれば OK です。

---

## 💎 特徴と設計思想

### 🎤 Whisper による文字レベル自動アライメント（align_subtitles.py）

- [stable-ts](https://github.com/jianfch/stable-ts) を使って Whisper の推論結果を文字レベルに分解します。
- ZIP 内の ASS から歌詞テキストだけを抽出して Whisper にアライメントさせ、句読点・記号は直前の文字の `\k` に吸収させます。
- 出力は pysubs2 互換の ASS ファイルで、スタイル情報は元ファイルから引き継ぎます。
- **歌い出し対応**: 1行目の開始時刻は「Whisper の判定」と「元 ASS の開始時刻」の早い方を採用します。元 ASS の1行目 Start を歌い出しに合わせておくと正確に表示されます。
- **繰り返し歌唱対応**: AI 音源が同じ歌詞を繰り返す場合、各行の終了時刻を次行開始直前まで延長し、繰り返し中も現在の歌詞を表示し続けます。

### 🎤 ASS カラオケ字幕の完全再現（burn_subs.py）

- `\k` タグを解析し、文字単位でハイライト色（黄）と待機色（白）を切り替えます。
- 文字ごとの遷移タイミングをすべて算出し、状態が変わるフレームのみを描画します。
- **フォント**: デフォルトで**源ノ角ゴシック（Source Han Sans VF）**を使用します（SIL Open Font License、YouTube 商用利用可）。インストールは `brew install --cask font-source-han-sans-vf`。

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

**簡易版（ZIP 内の ASS タイミングをそのまま使う）:**

```sh
python3 burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4
```

---

## ⚙️ 引数

### align_subtitles.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `audio.mp3` | ✅ | アライメント対象の音声ファイル |
| `keyframes.zip` または `subtitles.ass` | ✅ | ZIP を渡すと内部の `subtitles.ass` を自動抽出。ASS ファイルを直接渡すことも可 |
| `output.ass` | ➖ | 出力 ASS ファイル名（省略時: `subtitles_aligned.ass`）|
| `--model` | ➖ | Whisper モデルサイズ（`base` / `small` / `medium` / `large-v3`、省略時: `large-v3`）|

**注意:** 入力 ASS の歌詞行数と Whisper が検出した文字数が一致しない場合はエラーで停止します。その場合は歌詞テキストの表記（スペース・句読点）を Whisper の出力に合わせて調整してください。

### burn_subs.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `audio.mp3` | ✅ | BGM として使用する音声ファイル |
| `keyframes.zip` | ✅ | PNG 画像・`inputs.txt`・`subtitles.ass` を含む ZIP |
| `output.mp4` | ➖ | 出力ファイル名（省略時: `output.mp4`）|
| `--subs <subtitles.ass>` | ➖ | ZIP 内の字幕を上書きする ASS ファイル |

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
