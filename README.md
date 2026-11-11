# ✨ Lyric Video Maker

[![CI](https://github.com/shouni/lyric-video-maker/actions/workflows/ci.yml/badge.svg)](https://github.com/shouni/lyric-video-maker/actions/workflows/ci.yml)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen)](#)
[![Language](https://img.shields.io/badge/Language-Python-blue)](https://www.python.org/)
[![Python Version](https://img.shields.io/badge/Python-3.12%2B-blue)](https://www.python.org/)
[![GitHub tag (latest by date)](https://img.shields.io/github/v/tag/shouni/lyric-video-maker)](https://github.com/shouni/lyric-video-maker/tags)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

English | [日本語](README.ja.md)

## 🎯 Overview

**Lyric Video Maker** is a set of Python tools that turn a song into a music video with karaoke lyrics. There are two ways to use it.

- **beatmv (a music video drawn from the audio)**: no image assets. It analyses the song and draws kicks, snares, bass and lyrics frame by frame (4K by default). → [beatmv](#-beatmv-a-music-video-drawn-from-the-audio)
- **Karaoke video from a keyframe ZIP**: builds an **MP4 with karaoke subtitles** from an audio file (mp3, wav, …) and a ZIP of keyframes. It can also burn subtitles onto a finished music video.

The keyframe ZIP workflow has three steps (step ② has two variants, for still images or for video).

```
① align_subtitles.py
   audio.mp3 + keyframes.zip|subtitles.ass|lyrics.txt  →  ASS with karaoke timing (\k tags)

② burn_subs.py
   audio.mp3 + keyframes.zip (+ ASS)  →  output.mp4 (slideshow with karaoke subtitles)

②' burn_subs_video.py
   video.mp4 + ASS  →  MP4 with one-line styled subtitles burnt in, picture unchanged

③ make_cover.py (optional)
   output.mp4  →  two cover images (16:9 YouTube thumbnail / 9:16 Shorts)
```

If you already have a timed ASS file, run step ② only.
If your lyrics are plain text (.txt), pass them straight to step ①.


---

## 💎 Features and design

### 🎤 Character-level alignment with Whisper (align_subtitles.py)

- Uses [stable-ts](https://github.com/jianfch/stable-ts) to break Whisper's output down to single characters.
- Takes only the lyric text from the input (ZIP / ASS / txt), aligns it with Whisper, and folds punctuation and symbols into the `\k` of the character before.
- Writes a pysubs2-compatible ASS file and keeps the styles of the input file.
- **Plain-text lyrics**: give it a `.txt` file (one line = one subtitle line, blank lines are ignored) and it builds an ASS with the default Karaoke style (Arial 64px, yellow highlight, PlayRes 1920x1080) before aligning. **Your line breaks become the subtitle lines**, so break lines where they are sung.
- **First line**: only for the first line, if the input ASS starts **0–3 s earlier** than Whisper's guess, the input's start time is kept. Larger gaps, or Whisper being earlier, use Whisper's time. Setting the first line's Start to the sung entry in the input ASS gives the most accurate result.
- **Repeated singing**: AI vocals often repeat a line, so each line is extended until just before the next one starts and stays on screen while it repeats. When a line's 0.3 s tail overlaps the next line, it is cut at the next line's start (so libass does not stack two lines either).
- Lines made only of punctuation or symbols have nothing to align, so they keep the input ASS times. It warns rather than stops; merge such lines into the line before.

### 🎤 Full ASS karaoke rendering (burn_subs.py)

- Parses `\k` tags and switches each character between the highlight colour (sung) and the waiting colour. Colours come from the ASS style's Primary / Secondary (the default Karaoke style is yellow and white).
- **Diff rendering**: instead of drawing every frame, it draws a frame **only when the subtitle state (background image, visible line, number of highlighted characters) changes**, then joins them with ffmpeg's concat demuxer and adds the audio. A 3-minute video needs about 200 frames and finishes in tens of seconds.
- **Automatic scaling**: font size, margins and outline are computed from the ratio of the ASS `PlayResY` to the real image resolution, so nothing needs changing for another resolution.

### 🎬 Music-video styling with a style file (--style-file)

- `--style-file style.json` overrides the drawing style. Presets live in `styles/` (`rock.json` / `minimal.json` / `ballad.json` / `karaoke_blue.json`). `karaoke_blue` is the palette of my music videos (light blue #8FE3FF, white, navy outline).
- `"mode": "line"` shows **one line without karaoke highlighting**, with placement (`position`: top/center/bottom), letter spacing (`letter_spacing`) and a background box (`box`). It is faster than karaoke because the state changes only per line.
- Lines that do not fit are **adjusted automatically**: first it shrinks the font on one line, and when the shrink would go below 70 % it **wraps into two lines** (at a space for English, at the best-balanced character boundary for Japanese). Karaoke mode follows the same rule but splits only between `\k` segments, never inside one.
- Pixel values (`font_size` / `margin_v` / `outline` / `letter_spacing` / `box.pad`) are given **for PlayResY 1080** and scaled to the real resolution. Colours are `#RRGGBB`. Invalid values warn and fall back to the ASS values.

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

- `box` / `font` / `font_index` are optional. Without `mode` it renders karaoke as before and only applies the colour and size overrides.
- `font_index` picks a weight inside a `.ttc` (one file holding several weights), e.g. Hiragino Mincho ProN: 0 = W3, 2 = W6. It only applies together with `font`.
- **Fonts**: macOS's **Hiragino Kaku Gothic W7** is preferred. Without it, it falls back to **Source Han Sans** (Source Han Sans VF, SIL Open Font License, fine for commercial use on YouTube). Install it with `brew install --cask font-source-han-sans-vf`.

### 🖼️ Cover images (make_cover.py)

- Extracts a frame from the finished MP4 with ffmpeg (the first frame by default, or any time with e.g. `--at 8`), draws the title and artist name on it, and writes two images:
  - `cover_youtube.png` (1280x720): the recommended YouTube thumbnail size
  - `cover_short.png` (1080x1920): a 16:9 frame centred over a blurred, darkened background, for Shorts
- Without a title it uses **the MP4's parent folder name**. The default artist name is `Digital Armor Style`.
- White title with a dark stroke and shadow, artist name in gold. Long titles shrink until they fit.
- Fonts are found with `burn_subs.py`'s `load_font`, so covers use the same font as the subtitles.

---

## 🚀 Quick start

### Install

```sh
pip install -r requirements.txt
```

> ffmpeg is also required (for encoding).
> ```sh
> brew install ffmpeg   # macOS
> ```
>
> If you installed into a virtual environment, read `python3` below as `.venv/bin/python`
> (`make_cover.py` also uses pysubs2 / Pillow through `burn_subs.py`, so run it in the same environment).

---

### Run (copy and paste)

**Accurate (with Whisper alignment):**

```sh
# ① timing
python3 align_subtitles.py input/audio.mp3 input/keyframes.zip input/subtitles_aligned.ass

# ② video
python3 burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4 --subs input/subtitles_aligned.ass
```

**Lyrics as .txt only** (replace the input of ①; ② stays the same):

```sh
# lyrics.txt: one line = one subtitle line
python3 align_subtitles.py input/audio.mp3 input/lyrics.txt input/subtitles_aligned.ass
```

**Quick (use the ASS timing inside the ZIP as it is):**

```sh
python3 burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4
```

**②' Burn styled subtitles onto a finished music video:**

```sh
python3 burn_subs_video.py input/video.mp4 input/subtitles_aligned.ass output/video_with_lyrics.mp4 --style-file styles/rock.json
```

**③ Cover images (optional):**

```sh
python3 make_cover.py output/output.mp4 --title "Song Title"
```

---

## ⚙️ Arguments

### align_subtitles.py

| Argument | Required | Description |
| --- | --- | --- |
| `audio.mp3` | ✅ | Audio to align (wav etc. also work; a separated vocal track is more accurate) |
| `keyframes.zip` / `subtitles.ass` / `lyrics.txt` | ✅ | A ZIP has its `subtitles.ass` extracted automatically. An ASS file or plain-text lyrics (one line = one subtitle line) also work |
| `output.ass` | ➖ | Output ASS file (default: `subtitles_aligned.ass`) |
| `--model` | ➖ | Whisper model size (`base` / `small` / `medium` / `large-v3`, default: `large-v3`) |
| `--language` | ➖ | Language code of the lyrics passed to Whisper (default: `ja`) |

**Note:** if the number of lyric characters (without punctuation and symbols) differs from what Whisper detected, it stops with an error, because a one-character offset shifts every following line. If the vocals ad-lib or skip repeats, edit the lyric text to match what is sung.

### burn_subs.py

| Argument | Required | Description |
| --- | --- | --- |
| `audio.mp3` | ✅ | Audio used as the soundtrack (wav etc. also work) |
| `keyframes.zip` | ✅ | ZIP with PNG images, `inputs.txt` and `subtitles.ass` |
| `output.mp4` | ➖ | Output file (default: `output.mp4`) |
| `--subs <subtitles.ass>` | ➖ | ASS file that replaces the subtitles in the ZIP |
| `--style-file <style.json>` | ➖ | JSON that overrides the drawing style (presets in `styles/`) |

### burn_subs_video.py

| Argument | Required | Description |
| --- | --- | --- |
| `video.mp4` | ✅ | Input video (the picture is kept as it is) |
| `subtitles.ass` | ✅ | Timed ASS (`\k` tags are ignored; only each line's start and end are used) |
| `output.mp4` | ➖ | Output file (default: `output_video.mp4`) |
| `--style-file <style.json>` | ➖ | Style JSON (line mode only; `mode: "karaoke"` is ignored) |

Each line is drawn to a transparent PNG and composited at its time with ffmpeg's `overlay` and `enable='between(t,...)'`. The audio is copied without re-encoding.

### make_cover.py

| Argument | Required | Description |
| --- | --- | --- |
| `video.mp4` | ✅ | Input MP4 |
| `--title` | ➖ | Title (default: the MP4's parent folder name) |
| `--artist` | ➖ | Artist name (default: `Digital Armor Style`) |
| `--outdir` | ➖ | Output folder (default: the MP4's folder) |
| `--at <seconds>` | ➖ | Time of the frame used for the cover (default: `0`, the first frame). Use it when the video starts from black |

---

## 📦 ZIP layout

The ZIP holds the keyframe images, the timeline and the subtitles, so it alone is enough to rebuild the video.

```
keyframes.zip
├── cut_01.png       # keyframe images
├── cut_02.png
├── ...
├── inputs.txt       # image file names and how long each is shown
└── subtitles.ass    # ASS karaoke subtitles
```

**inputs.txt format:**

```
file 'cut_01.png'
duration 40.000

file 'cut_02.png'
duration 50.000
```

---

## 🥁 beatmv: a music video drawn from the audio

It analyses the song and draws, frame by frame with Pillow, a ring that widens on kicks, vertical lines that open on percussion, a timeline of hits, and lyrics that fill in as they are sung. 4K (3840x2160) by default: YouTube serves 1440p and above at a higher bitrate, so text edges stay sharper even for people watching at 1080p.

Everything that differs per song goes in `songs/<slug>/song.toml`; the code is shared by every song. `songs/` and the generated `work/` stay on the local machine and are not tracked by git (so lyrics and audio never land in a public repository). See `tests/fixtures/song/` for an example.

```
songs/<slug>/
  song.toml     title, audio, work directory, section times, drum rule
  beats.json    downbeat times (from Suno's audio-analysis)
  lyrics.ass    lyrics with \k (from Suno's lyric alignment, or align_subtitles.py)
```

```sh
.venv/bin/python -m beatmv analyze  songs/<slug>   # Demucs drum separation → kick/snare hits, loudness for the waveform
.venv/bin/python -m beatmv align    songs/<slug>   # write lyrics.ass (suno: Suno's alignment only / hybrid: combined with Whisper)
.venv/bin/python -m beatmv render   songs/<slug>   # the video (4K by default; --scale 1 for 1080p, --preview 45,100 for stills)
.venv/bin/python -m beatmv srt      songs/<slug>   # YouTube subtitles (SRT)
.venv/bin/python -m beatmv thumb-bg songs/<slug>   # thumbnail background (no text; the title is added by my own composing pipeline)
```

- **Take times from the audio, not from the recipe.** Suno does not keep the section lengths it was asked for and sometimes adds interludes nobody asked for. Write the real times, from Suno's alignment and analysis, into `sections` / `choruses` / `instrumental`.
- **Pick the drum rule per song** (`drum_rule`). `rock` calls a hit a kick when the low end dominates. `edm` calls it a kick when the low end rises, because a four-on-the-floor kick's attack also reaches the mids, and shows the second row as general percussion (`PERC`). `trance` uses the same rule as `edm` but does not count kicks as percussion (with `edm`, the kick's attack also counts as percussion, so the vertical lines and the ring flash together on the beat). Keep `edm` for songs with claps on the kick.
- **`dnb` takes kicks and ghost notes out of the snare row.** Kicks use the `rock` rule; the snare row drops hits that coincide with a kick and hits with a weak attack (ghost notes, hats). Those are shown on the snare row as small light-blue stars and do not light the centre line (radius 11: smaller than a strong snare's square, still visible at phone size). Across six DnB songs, snare flashes per bar fell from 2.5–3.4 to 1.3–1.8, and more of them land on beats 2 and 4 (half-time songs hit beat 3, so they do not change).
- **Bass is shown as a band of loudness** (only with `bass_band = true`). Demucs's 4-stem split isolates the bass, and a horizontal band under the waveform grows and shrinks with its loudness. It is not drawn as hits because an off-beat bass line has few clear attacks and only part of what plays was picked up. A third row for claps and hi-hats was tried too, but claps on 2 and 4 could not be told apart from the kick and only some off-beat hats were found, so it was left out.
- **Colours are shared with burn_subs through `styles/`** (`style`, default `karaoke_blue`). beatmv only reads colours: `primary_color` (sung), `secondary_color` (not yet sung), `outline_color` (outline and the lower background), plus its own `accent_color` (ring, waveform, timeline, title; defaults to `primary_color`) and `background_top` (the upper background). Sizes such as `font_size` are ignored, because beatmv's layout decides text size.
- **For songs sung from kana, set `lyrics_aligner = "hybrid"` and build lyrics.ass with `beatmv align`.** Each line's start and end come from Suno's alignment (`work/<slug>/suno_alignment.json`); the timing of characters inside the line comes from Whisper (`work/<slug>/lyrics.whisper.ass`, the output of align_subtitles.py). Suno places lines accurately but returns the text in kana; Whisper can time kanji but drifts at the ends of lines. If the line counts differ it stops rather than guess, and lines with a large offset are listed for checking, so only those need checking by ear.
- **For songs sung in English, set `lyrics_aligner = "suno"` and build lyrics.ass with `beatmv align`.** Suno's aligned words are the lyrics as displayed, so each word is filled in turn. Gaps between words become empty segments, so the next word starts filling when it is sung. Suno attaches a word's trailing punctuation to the next word (`ready` / `! Go`), so it is moved back onto the word before; otherwise the `!` stays unfilled until the next word. The alignment can also go wrong (squeezing the last two lines into one second, holding one word for 12 seconds), so compare it with the separated vocal's loudness (`vocal_rms.npy`) and, if they disagree, fix `suno_alignment.json` before running `beatmv align`.
- **Show a translation with `translation`** (a text file next to song.toml: one line per subtitle line, an empty line means no translation for that line). The translation is not filled in; it appears under the lyric with its line and hides the next-line preview meanwhile. If the line counts differ it stops. `beatmv srt` also writes the translation as SRT (`<slug>.ja.srt`).
- **Latin-only lines use a Latin typeface.** Lines containing Japanese use Hiragino; Latin-only lines (English lyrics, English translations, titles, section names) use Helvetica Neue. A Latin typeface looks smaller than Hiragino's Latin letters at the same point size, so it is scaled by 1.18 to keep the line width. `BEATMV_EN_FONT=sf` (SF Pro, ×1.25) or `hiragino` (the previous look) switches it.
- **Choose the waveform source with `wave`**: `mix` (everything except drums, default) or `vocals` (voice only; needs the Demucs 4-stem split).
- **Hook lines stand out by size and colour.** English lines of up to four words without Japanese (e.g. `Override! Override!`) are drawn larger, filled with `hook_color`, and the waveform leans to `hook_color` around them. No flashing or beat zoom.
- **Where the lyrics come from changes how filling starts** (`lyrics_aligner`). With `whisper` (align_subtitles.py), a line's start is pulled back to the end of the previous line, so when the first character is long, filling starts just before it is sung.

---

## 🤝 Dependencies

| Package | Used for |
| --- | --- |
| [stable-ts](https://github.com/jianfch/stable-ts) | Character-level alignment with Whisper |
| [Pillow](https://pillow.readthedocs.io/) | Drawing subtitles onto PNG images |
| [pysubs2](https://pysubs2.readthedocs.io/) | Parsing ASS subtitles and styles |
| [ffmpeg](https://ffmpeg.org/) | Video encoding and audio muxing (system install) |
| [Demucs](https://github.com/adefossez/demucs) | Drum separation for beatmv |
| [librosa](https://librosa.org/) | Hit detection and loudness for beatmv |

---

## 🧪 Tests

```sh
pip install -r requirements-dev.txt
python3 -m pytest tests -q
```

The tests cover the pure logic that does not call ffmpeg or Whisper: `\k` parsing, line wrapping, style JSON validation, matching lyrics to the audio's characters, resolving overlapping display times, and beatmv's song settings, drum rules, lyric timing and colours. beatmv is tested with the made-up song in `tests/fixtures/song/`. Check the video itself by running on the samples in `input/`.

---

## 📜 License

This project is released under the [MIT License](https://opensource.org/licenses/MIT).
