# ✨ Lyric Video Maker

[![Language](https://img.shields.io/badge/Language-Python-blue)](https://www.python.org/)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen)](#)

## 🎯 概要

**Lyric Video Maker** は、MP3 音声ファイルとキーフレーム ZIP ファイルから、**カラオケ字幕付き MP4 動画**を生成する Python スクリプトです。

ZIP には PNG 画像・タイムライン定義 (`inputs.txt`)・ASS 字幕 (`subtitles.ass`) が含まれており、この 2 ファイルを渡すだけで字幕を焼き込んだ動画を出力できます。

```
audio.mp3 + keyframes.zip  →  output.mp4（カラオケ字幕付き）
```

---

## 💎 特徴と設計思想

### 🎤 ASS カラオケ字幕の完全再現

- `\k` タグを解析し、文字単位でハイライト色（黄）と待機色（白）を切り替えます。
- 16 行・文字ごとの遷移タイミングをすべて算出し、状態が変わるフレームのみを描画します。

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

### 実行

```sh
python3 burn_subs.py <audio.mp3> <keyframes.zip> [output.mp4]
```

**例:**

```sh
python3 burn_subs.py audio.mp3 keyframes-video-recipe.zip output.mp4
```

出力ファイル名を省略すると `output.mp4` に保存されます。

---

## ⚙️ 引数

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
| [Pillow](https://pillow.readthedocs.io/) | PNG 画像への字幕描画 |
| [pysubs2](https://pysubs2.readthedocs.io/) | ASS 字幕ファイルのパース・スタイル取得 |
| [ffmpeg](https://ffmpeg.org/) | 動画エンコード・音声合成（システムインストール） |

---

## 📜 ライセンス (License)

このプロジェクトは [MIT License](https://opensource.org/licenses/MIT) の下で公開されています。
