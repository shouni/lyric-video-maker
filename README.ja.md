# ✨ Lyric Video Maker

[![CI](https://github.com/shouni/lyric-video-maker/actions/workflows/ci.yml/badge.svg)](https://github.com/shouni/lyric-video-maker/actions/workflows/ci.yml)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen)](#)
[![Language](https://img.shields.io/badge/Language-Python-blue)](https://www.python.org/)
[![Python Version](https://img.shields.io/badge/Python-3.12%2B-blue)](https://www.python.org/)
[![GitHub tag (latest by date)](https://img.shields.io/github/v/tag/shouni/lyric-video-maker)](https://github.com/shouni/lyric-video-maker/tags)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

[English](README.md) | 日本語

## 🎯 概要

**Lyric Video Maker** は、曲から MV と歌詞字幕を作る Python のツール群です。作り方は 2 通りあります。

- **beatmv（音から描く MV）**：画像素材を使わず、曲を解析してキック・スネア・ベース・歌詞を 1 コマずつ描きます（4K）。→ [beatmv](#-beatmv画像素材なしで音から描く-mv)
- **キーフレーム ZIP からカラオケ動画**：音声ファイル（mp3 / wav など）とキーフレーム ZIP から、**カラオケ字幕付き MP4 動画**を作ります。完成済みの MV 動画へ字幕を乗せることもできます。

キーフレーム ZIP からカラオケ動画を作るワークフローは 3 段階です（② は素材が静止画か動画かで2種類）。

```
① align_subtitles.py
   audio.mp3 + keyframes.zip|subtitles.ass|lyrics.txt  →  カラオケタイミング付き ASS（\k タグ）

② burn_subs.py
   audio.mp3 + keyframes.zip (+ ASS)  →  output.mp4（カラオケ字幕付きスライドショー）

②' burn_subs_video.py
   video.mp4 + ASS  →  映像を保ったままスタイル字幕（1行表示またはカラオケ）を焼き込んだ MP4

③ make_cover.py（任意）
   output.mp4  →  カバー画像 2 枚（YouTube サムネイル用 16:9 / ショート用 9:16）
```

既にタイミング済みの ASS がある場合は ② だけ実行すれば OK です。
歌詞がプレーンテキスト（.txt）しかない場合も ① にそのまま渡せます。


---

## 💎 特徴と設計思想

### 🎤 Whisper による文字レベル自動アライメント（align_subtitles.py）

- [stable-ts](https://github.com/jianfch/stable-ts) を使って Whisper の推論結果を文字レベルに分解します。
- 入力（ZIP / ASS / txt）から歌詞テキストだけを抽出して Whisper にアライメントさせ、句読点・記号は直前の文字の `\k` に吸収させます。
- 出力は pysubs2 互換の ASS ファイルで、スタイル情報は元ファイルから引き継ぎます。
- **プレーンテキスト歌詞対応**: `.txt`（1行=1字幕行、空行は無視）を渡すと、既定の Karaoke スタイル（Arial 64px・黄色ハイライト・PlayRes 1920x1080）で ASS を自動合成してからアライメントします。**行の区切りがそのまま字幕の行割りになる**ため、歌唱単位で改行してください。
- **歌い出し対応**: 1行目のみ、元 ASS の開始時刻が Whisper の判定より **0〜3秒だけ早い**場合に限り、元 ASS の開始時刻を採用します。3秒を超える差がある場合や Whisper の方が早い場合は Whisper の判定を使用します。元 ASS の1行目 Start を歌い出しに合わせておくと正確に表示されます。
- **繰り返し歌唱対応**: AI 音源が同じ歌詞を繰り返す場合、各行の終了時刻を次行開始直前まで延長し、繰り返し中も現在の歌詞を表示し続けます。逆に、行末の余韻（0.3秒）で次行と重なった場合は次行の開始で切ります（libass に直接渡しても2行が積まれません）。
- 句読点・記号だけの行はアライメントの対象文字を持たないため、タイミングを割り当てられず入力 ASS の時刻がそのまま残ります。中断はせず警告を出すので、そうした行は前の行にまとめてから渡してください。

### 🎤 ASS カラオケ字幕の完全再現（burn_subs.py）

- `\k` タグを解析し、文字単位でハイライト色（発声済み）と待機色を切り替えます。色は ASS スタイルの Primary / Secondary から読みます（既定の Karaoke スタイルは黄と白）。
- **差分描画**: フレームを1枚ずつ作るのではなく、**字幕状態（背景画像・表示行・ハイライト済み文字数）が変化するタイミングだけ**を描画し、ffmpeg の concat demuxer でつないで音声と合成します。3分の動画でも 200 枚ほどの差分フレームで済み、数十秒で完了します。
- **自動スケーリング**: フォントサイズ・マージン・アウトラインは ASS の `PlayResY` と実際の画像解像度の比から計算するため、解像度を変えても設定変更は不要です。

### 🎬 スタイルファイルによる MV 風描画（--style-file）

- `--style-file style.json` で描画スタイルを上書きできます。`styles/` にプリセット（`rock.json` / `minimal.json` / `ballad.json` / `karaoke_blue.json`）あり。`karaoke_blue` はこれまでの MV で使ってきた配色（水色 #8FE3FF・白・紺の縁取り）です。
- `"mode": "line"` にするとカラオケハイライトなしの **1行表示**になります。描画状態が行単位になるためカラオケより高速です。
- 配置（`position`: top/center/bottom）・字間（`letter_spacing`）・背景座布団（`box`）は**どちらのモードでも使えます**。カラオケと1行表示は同じ配置処理を通るので、ハイライトの有無にかかわらず同じ見た目になります。
- 幅に収まらない行は**自動調整**します: まず1行のままフォント縮小を試み、縮小率が 70% を下回るほど長い行は**2行に折り返し**ます（英語はスペース位置、日本語は幅バランスの良い文字境界で分割）。カラオケモードも同じ方針で、こちらは `\k` セグメントを分割単位にします（セグメント内部では割りません）。
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
  "font": "/path/to/font.ttf",
  "font_index": 0
}
```

- `box` / `font` / `font_index` は任意です。`mode` 省略時は従来どおりカラオケ描画で、色やサイズの上書きのみ適用されます。
- `font_index` は `.ttc`（1ファイルに複数ウェイトが入った形式）でウェイトを選ぶための番号です（例: ヒラギノ明朝 ProN は 0=W3 / 2=W6）。`font` を指定したときだけ効きます。
- **フォント**: macOS 標準の**ヒラギノ角ゴシック W7**を最優先で使用します。見つからない場合は**源ノ角ゴシック**（Source Han Sans VF）にフォールバックします（SIL Open Font License、YouTube 商用利用可）。Source Han Sans VF のインストールは `brew install --cask font-source-han-sans-vf`。Linux では Noto Sans CJK（`fonts-noto-cjk`）を探します。別のフォントを全体で使うには `BURN_SUBS_FONT=/path/to/font.ttf` を設定します。組み込みの一覧より先に試します（カバー画像も従い、beatmv はヒラギノがないときに従います）。

### 🖼️ カバー画像の自動生成（make_cover.py）

- 完成した MP4 のフレーム（既定は先頭。`--at 8` のように秒を指定すると任意の位置）を ffmpeg で抽出し、タイトルとアーティスト名を合成した画像を 2 枚出力します。
  - `cover_youtube.png`（1280x720）: YouTube サムネイル推奨サイズ
  - `cover_short.png`（1080x1920）: ぼかし+減光した背景の中央に 16:9 フレームを重ねたショート用
- タイトル省略時は **MP4 の親フォルダ名**を自動で使用します。アーティスト名の既定は `Digital Armor Style`。
- 白タイトル+ダークストローク+シャドウ、アーティスト名は金色。長いタイトルは幅に収まるまで自動縮小します。
- フォント探索は `burn_subs.py` の `load_font` をそのまま利用しているため、字幕と同じフォントで描画されます。

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
>
> 仮想環境に入れた場合は、以下の `python3` を `.venv/bin/python` に読み替えてください
> （`make_cover.py` も `burn_subs.py` 経由で pysubs2 / Pillow を使うため、同じ環境で実行する必要があります）。

別のプロジェクトから使うときは、ファイルをコピーせずモジュールとしてインストールします（`import burn_subs`・`burn_subs_video`・`make_cover`・`align_subtitles`。beatmv はインストールされず、チェックアウトから実行します）。

```sh
pip install "git+https://github.com/shouni/lyric-video-maker"                                   # 焼き込みとカバー画像
pip install "lyric-video-maker[align] @ git+https://github.com/shouni/lyric-video-maker"        # + Whisper の時刻合わせ
```

---

### 実行（コピペ用）

**精度重視（Whisper アライメントあり）:**

```sh
# ① タイミング生成
python3 align_subtitles.py input/audio.mp3 input/keyframes.zip input/subtitles_aligned.ass

# ② 動画生成
python3 burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4 --subs input/subtitles_aligned.ass
```

**歌詞が .txt しかない場合**（① の入力を差し替えるだけ。② は上と同じ）:

```sh
# lyrics.txt は 1行=1字幕行
python3 align_subtitles.py input/audio.mp3 input/lyrics.txt input/subtitles_aligned.ass
```

**簡易版（ZIP 内の ASS タイミングをそのまま使う）:**

```sh
python3 burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4
```

**②' 完成済み動画（MV）にスタイル字幕を焼き込む:**

```sh
python3 burn_subs_video.py input/video.mp4 input/subtitles_aligned.ass output/video_with_lyrics.mp4 --style-file styles/rock.json
python3 burn_subs_video.py input/video.mp4 input/subtitles_aligned.ass output/video_karaoke.mp4 --karaoke   # 文字ごとのハイライト
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
| `audio.mp3` | ✅ | アライメント対象の音声ファイル（wav なども可。分離したボーカルを渡すと精度が上がる）|
| `keyframes.zip` / `subtitles.ass` / `lyrics.txt` | ✅ | ZIP を渡すと内部の `subtitles.ass` を自動抽出。ASS ファイル直接、またはプレーンテキスト歌詞（1行=1字幕行）も可 |
| `output.ass` | ➖ | 出力 ASS ファイル名（省略時: `subtitles_aligned.ass`）|
| `--model` | ➖ | Whisper モデルサイズ（`base` / `small` / `medium` / `large-v3`、省略時: `large-v3`）|
| `--language` | ➖ | Whisper に渡す歌詞の言語コード（省略時: `ja`）|

**注意:** 句読点・記号を除いた歌詞の文字数と、Whisper が検出した文字数が一致しない場合はエラーで停止します（1文字ずれるだけで以降の行が全部ずれるため）。歌詞にアドリブや繰り返しの省略がある場合は、歌唱に合わせて歌詞テキストを直してください。エラーには、最初に食い違った行と音声上の時刻が出ます（`first difference at line 12 (…): the lyrics have '…' where the audio gave '…' (around 48.2s)`）。そこから確認してください。

### burn_subs.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `audio.mp3` | ✅ | BGM として使用する音声ファイル（wav なども可）|
| `keyframes.zip` | ✅ | PNG 画像・`inputs.txt`・`subtitles.ass` を含む ZIP |
| `output.mp4` | ➖ | 出力ファイル名（省略時: `output.mp4`）|
| `--subs <subtitles.ass>` | ➖ | ZIP 内の字幕を上書きする ASS ファイル |
| `--style-file <style.json>` | ➖ | 描画スタイルを上書きする JSON（`styles/` にプリセットあり）|

### burn_subs_video.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `video.mp4` | ✅ | 入力動画（映像はそのまま保持される） |
| `subtitles.ass` | ✅ | タイミング付き ASS（1行表示は行の開始/終了のみ使用。カラオケは `\k` タグも使用） |
| `output.mp4` | ➖ | 出力ファイル名（省略時: `output_video.mp4`）|
| `--style-file <style.json>` | ➖ | 描画スタイル JSON。`mode` がなければ1行表示（既定がカラオケの `burn_subs.py` とは逆）。`"mode": "karaoke"` で `\k` ごとにハイライト |
| `--karaoke` | ➖ | スタイルの指定にかかわらず、`burn_subs.py` と同じく `\k` ごとにハイライトする |

字幕の状態ごと（1行表示は行ごと、カラオケはハイライト済みの文字数ごと）に透過 PNG を描き、ffmpeg の concat demuxer で1本の透過トラックにつないで、`overlay` 1回で動画に重ねます。字幕の切り替わりは `burn_subs.py` と同じく 1/25 秒刻みになります。音声は再エンコードせずコピーします。

### make_cover.py

| 引数 | 必須 | 説明 |
| --- | --- | --- |
| `video.mp4` | ✅ | 入力 MP4 |
| `--title` | ➖ | タイトル（省略時: MP4 の親フォルダ名）|
| `--artist` | ➖ | アーティスト名（省略時: `Digital Armor Style`）|
| `--outdir` | ➖ | 出力フォルダ（省略時: MP4 と同じフォルダ）|
| `--at <秒>` | ➖ | カバーに使うフレームの時刻（省略時: `0`＝先頭）。先頭が暗転している動画で指定する |

---

## 📦 ZIP ファイルの構成

キーフレーム画像・タイムライン・字幕がすべて同梱されているため、この ZIP 1つで動画を再生成できます。

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

## 🥁 beatmv：画像素材なしで音から描く MV

曲を解析して、キックで広がるリング・打楽器で開く縦線・打点のタイムライン・歌った分だけ塗られる歌詞を、Pillow で 1 コマずつ描きます。既定は 4K（3840x2160）。YouTube は 1440p 以上の投稿を高ビットレートで配信するので、1080p で見る人にも文字の輪郭が残りやすくなります。

曲ごとの違いは `songs/<slug>/song.toml` に書き、コードは全曲で共有します。`songs/` と生成物の `work/` は手元のマシンにだけ置き、git では管理しません（曲の歌詞や音源を公開リポジトリに載せないため）。書き方の例は `tests/fixtures/song/` にあります。

```
songs/<slug>/
  song.toml     曲名・音源・作業ディレクトリ・セクションの時刻・ドラムの振り分け方
  beats.json    小節頭の時刻（Suno の audio-analysis から）
  lyrics.ass    \k 付きの歌詞（Suno の時刻合わせ、または align_subtitles.py で作る）
```

```sh
.venv/bin/python -m beatmv analyze  songs/<slug>   # Demucs でドラム分離 → キック/スネアの打点、波形用の音量
.venv/bin/python -m beatmv align    songs/<slug>   # lyrics.ass を作る（suno: Suno の時刻合わせだけ / hybrid: Whisper と組み合わせる）
.venv/bin/python -m beatmv render   songs/<slug>   # MV（既定 4K。--scale 1 で 1080p、--preview 45,100 で静止画）
.venv/bin/python -m beatmv render   songs/<slug> --portrait --from 132 --to 198.2   # ショート: 1080x1920、その区間だけ
.venv/bin/python -m beatmv srt      songs/<slug>   # YouTube 字幕（SRT）
.venv/bin/python -m beatmv thumb-bg songs/<slug>   # サムネイルの背景（文字なし。曲名は自作の作曲パイプラインで載せる）
```

- **`--portrait` で同じ曲を 9:16（ショート用）に描く**（既定 1080x1920、`--scale 2` で 2160x3840）。ヘッダーはショートの上部バーより下、タイムラインは下部 UI より上に置きます。最小の文字サイズでも幅に収まらない歌詞や訳は2行に折り返し（英語は単語の間で）、上の行から順に塗られます。
- **`--from` / `--to` で曲の一部だけを描く**（横・縦どちらでも）。変更の確認に 4K で全曲を描かずに数秒だけ出す、ショートの区間を切り出す、のどちらにも使えます。曲の途中から切り出した区間は 0.08 秒でフェードインし、1.4 秒でフェードアウトします（映像も音も）。端が曲の端と同じなら曲のフェードのままです。ファイル名に区間が入ります（`mv_<slug>_portrait_132-198.2.mp4`）。
- **時刻はレシピではなく音から取る。** Suno はセクションの秒数を守らず、頼んでいない間奏を足すことがあります。`sections` / `choruses` / `instrumental` には、Suno の時刻合わせと解析から取った実際の時刻を書きます。
- **ドラムの振り分けは曲で選ぶ**（`drum_rule`）。`rock` は低域が主役の打点をキック、`edm` は四つ打ちのキックのアタックが中高域にも出るので低域が立てばキックとし、2 段目は打楽器全般（`PERC`）として表示します。`trance` は `edm` と同じ基準で、キックになった打点を打楽器に数えません（`edm` ではキックのアタックが打楽器にも数えられ、表拍で縦線とリングが同時に光ります）。キックと同時のクラップが鳴る曲は `edm` のままにします。
- **`dnb` はスネアからキックとゴーストノートを外す。** キックは `rock` と同じ基準で取り、スネアからはキックと重なる打点と、立ち上がりの弱い打点（ゴーストノート・ハット）を外します。外した打点はスネアの段に水色の小さな星で出し、画面中央の縦線は光らせません（星の半径は、強いスネアの四角より目立たず、スマホの大きさでも見える 11）。DnB の 6 曲で、スネアが 1 小節に光る回数が 2.5〜3.4 回から 1.3〜1.8 回になり、2・4 拍に乗る割合が上がりました（Half-Time の曲は 3 拍目なので上がりません）。
- **ベースは音量の帯で見せる**（`bass_band = true` のときだけ）。Demucs の 4 分割でベースを取り出し、その音量に合わせて波形の下の横帯が伸び縮みします。打点（1 音ずつ）にしないのは、裏打ちのベースが続くと音の立ち上がりが立たず、鳴っている音の一部しか拾えなかったためです。クラップとハイハットを分けて 3 段目にする案も試しましたが、2・4 拍のクラップはキックと重なって区別できず、裏拍のハイハットも一部しか取れなかったので入れていません。
- **配色は `styles/` を burn_subs と共有する**（`style`、既定 `karaoke_blue`）。beatmv が使うのは色だけで、`primary_color`（歌った部分）・`secondary_color`（まだの部分）・`outline_color`（縁取りと背景の下の色）に加え、beatmv だけの `accent_color`（リング・波形・タイムライン・曲名。省略時は `primary_color`）と `background_top`（背景の上の色）を読みます。`font_size` などの大きさは beatmv では使いません（文字の大きさは beatmv のレイアウトで決めるため）。
- **カナで歌わせた曲は `lyrics_aligner = "hybrid"` にし、`beatmv align` で lyrics.ass を作る。** 行の開始と終わりは Suno の時刻合わせ（`work/<slug>/suno_alignment.json`）、行の中の文字の配分は Whisper（`work/<slug>/lyrics.whisper.ass`、align_subtitles.py の出力。このファイルがなければ、`beatmv align` が `work/<slug>/lyrics.txt`（または `--lyrics`）から時刻合わせを自分で実行します。`analyze` が分離したボーカルがあればそれに合わせます）から取ります。Suno は行の位置が正確でも文字がカナで返り、Whisper は漢字に時刻を振れても行の両端がずれるためです。行数が合わなければ推測で合わせずに中断し、ずれの大きい行は「確認」として出すので、その行だけ耳で確かめます。
- **英語で歌わせた曲は `lyrics_aligner = "suno"` にし、`beatmv align` で lyrics.ass を作る。** Suno の時刻合わせの語がそのまま字幕になるので、語ごとに塗ります。語と語の間の空きは文字のない区間にするので、次の語は歌い出しから塗り始めます。Suno は語の後ろの句読点を次の語の頭に付けて返す（`ready` / `! Go`）ので、句読点は前の語へ戻してから塗ります。戻さないと、`!` だけが次の語まで白く残ります。時刻合わせが崩れることもある（最後の 2 行を 1 秒に詰める、1 語を 12 秒伸ばすなど）ので、分離した声の音量（`vocal_rms.npy`）と見比べ、合わなければ `suno_alignment.json` を直してから `beatmv align` します。
- **歌詞の訳は `translation` で出す**（song.toml と同じディレクトリのテキスト。字幕 1 行につき 1 行、空の行はその行の訳を出さない）。訳は塗らずに歌詞の下へ行と一緒に出し、そのあいだは次の行の予告を出しません。行数が字幕と違えば中断します。`beatmv srt` は訳の SRT（`<slug>.ja.srt`）も書きます。
- **英字だけの行は英字専用の書体で描く。** 日本語を含む行はヒラギノ、英字だけの行（英語の歌詞・英訳・曲名・セクション名など）は Helvetica Neue です。英字専用の書体は同じポイント数だとヒラギノの英字より小さく見えるので、行の幅がそろうよう 1.18 倍にしています。`BEATMV_EN_FONT=sf`（SF Pro、1.25 倍）か `hiragino`（従来どおり）で替えられます。
- **波形の元は `wave` で選ぶ**：`mix`（ドラム以外の音、既定）か `vocals`（声だけ。Demucs の 4 分割が要る）。
- **フックの行は文字の大きさと色で目立たせる。** 日本語を含まない 4 語以内の英語の行（`Override! Override!` など）は文字を大きめにし、歌った部分を `hook_color` で塗り、その前後で波形の色も `hook_color` へ寄せます。点滅や拍ズームは使いません。
- **歌詞の出どころで塗り始めの扱いが違う**（`lyrics_aligner`）。`whisper`（align_subtitles.py）は行の開始が前の行の終わりまで繰り上がるので、最初の文字が長いときは歌い出しの直前から塗ります。

---

## 🤝 依存関係 (Dependencies)

| パッケージ | 用途 |
| --- | --- |
| [stable-ts](https://github.com/jianfch/stable-ts) | Whisper による文字レベル音声アライメント |
| [Pillow](https://pillow.readthedocs.io/) | PNG 画像への字幕描画 |
| [pysubs2](https://pysubs2.readthedocs.io/) | ASS 字幕ファイルのパース・スタイル取得 |
| [ffmpeg](https://ffmpeg.org/) | 動画エンコード・音声合成（システムインストール） |
| [Demucs](https://github.com/adefossez/demucs) | beatmv のドラム分離 |
| [librosa](https://librosa.org/) | beatmv の打点検出・音量 |

---

## 🧪 テスト

```sh
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

ffmpeg や Whisper を呼ばない純粋ロジック（`\k` タグの解析、行の自動折り返し、スタイル JSON の検証、歌詞と音声の文字照合、表示区間の重なり解消、beatmv の曲設定・ドラムの振り分け・歌詞の時刻・配色など）を対象にしています。動画に重ねる透過字幕トラック、カバー画像のレイアウト、beatmv のレイアウト（横・縦・区間）も含みます。beatmv は `tests/fixtures/song/` の架空の曲で確かめます。動画の出来そのものは `input/` のサンプルで実行して確認してください。

---

## 📜 ライセンス (License)

このプロジェクトは [MIT License](https://opensource.org/licenses/MIT) の下で公開されています。
