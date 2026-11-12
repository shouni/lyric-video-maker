# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Conventions

- **Write code comments, docstrings, CLI help, log and error messages in English.** This is a public repository and the people asking how it works read English. The whole codebase was moved to English on 2026-11-09; do not mix Japanese back in. Japanese stays only where it is data: lyrics in tests and fixtures, song titles quoted as evidence (e.g. 境界の調律者), and the Hiragino font paths.
- **`README.md` is English and `README.ja.md` is its Japanese version.** When one changes, update the other in the same commit.
- Keep the "why" in comments (the song or measurement a constant was tuned on). Translate it; never drop it.

## Commands

There is no Makefile. The repository is four flat Python scripts plus the `beatmv/` package, which draws a music video from the audio; running them is the end-to-end check.
Only the pure logic that does not call ffmpeg/Whisper is covered by pytest (`tests/`).

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check .   # lint (settings in pyproject.toml; CI runs both)
```

CI (`.github/workflows/ci.yml`) runs the tests on macOS, because the rendering tests draw with Hiragino and Helvetica Neue, with only pysubs2 / pillow / numpy / pytest installed. Keep heavy imports (torch, librosa, stable-ts) inside the functions that need them, or CI has to install them just to import a module.

Dependencies are installed in `.venv` (Python 3.14). **Python 3.12+ is required**: the pinned numpy and pysubs2 need it (beatmv's `tomllib` alone would allow 3.11). **Always run with `.venv/bin/python`.** The system python3 has no pysubs2 / stable-ts. ffmpeg is a system install (brew).

```bash
# ① lyric alignment: audio + lyrics → ASS with karaoke timing
.venv/bin/python align_subtitles.py input/audio.mp3 input/keyframes.zip input/subtitles_aligned.ass
# lyrics may be keyframes.zip / .ass / .txt (one line = one subtitle line)

# ② burn subtitles: audio + keyframes.zip (+ ASS) → MP4
.venv/bin/python burn_subs.py input/audio.mp3 input/keyframes.zip output/output.mp4 --subs input/subtitles_aligned.ass

# ②' burn styled subtitles onto a finished video (picture kept; plain lines by default, --karaoke for \k highlighting)
.venv/bin/python burn_subs_video.py input/video.mp4 subtitles_aligned.ass out.mp4 --style-file styles/rock.json

# ③ cover images: an MP4 frame → cover_youtube.png (1280x720) + cover_short.png (1080x1920)
.venv/bin/python make_cover.py output/output.mp4 --title "Song Title"        # first frame
.venv/bin/python make_cover.py output/output.mp4 --title "Song Title" --at 8  # frame at 8 s
.venv/bin/python make_cover.py output/output.mp4 --title "Song Title" --artist-color 8FE3FF  # light-blue artist name
```

For a faster check use `--model small` (default `large-v3`; models are cached in `~/.cache/whisper/`). Samples in `input/`: `chorus.wav` (32 s), `chorus_lyrics.txt`, `chorus_aligned.ass` (8 lines), `keyframes.zip` (4 cuts), `video*.mp4`.

For refactors that touch rendering, render the same input before and after and compare frame hashes (x264 output is identical for identical input):

```bash
.venv/bin/python burn_subs.py input/chorus.wav input/keyframes.zip /tmp/a.mp4 --subs input/chorus_aligned.ass
ffmpeg -v error -i /tmp/a.mp4 -map 0:v -f framemd5 - | grep -v '^#' | md5
```

For beatmv, hash `render.render_image(t).tobytes()` at a few dozen times on real songs before and after instead (no video needed, nothing written to `work/`).

## Architecture

Local post-processing tools for the AP MV ecosystem (the side that produces keyframes.zip). A ① → ② → ③ pipeline that passes files between steps. `beatmv/` is a separate line that does not use keyframes.zip; it shares burn_subs.py's `parse_karaoke` and style loading.

**Sister repository**: `~/PycharmProjects/lyric-video` turns this repository into a Cloud Run web service; its `app/align_subtitles.py` / `app/burn_subs.py` are copies of the ones here (differing mostly in Linux font paths for Docker). burn_subs.py was restructured in e7f39e1 (`fit_units` / `best_split_index`), so the code shapes differ though the behaviour is the same. When fixing one, consider whether the other needs the port. `pyproject.toml` now packages the four scripts as importable modules (with the Linux font paths and `$BURN_SUBS_FONT` in `burn_subs.py`), so the sister repository can depend on this one instead of copying; it has not been switched over yet.

### align_subtitles.py — forced alignment (not transcription)

stable-ts `model.align()` **assigns the existing lyric text to the audio's timing**. There is no transcribe-from-scratch path.

Everything except the Whisper call is split into pure functions (`load_source_subs` → `extract_lyric_lines` → `map_chars_to_lines` → `build_karaoke_text` → `build_aligned_subs` → `fill_repeat_gaps` → `trim_overlaps`). Update `tests/test_align_subtitles.py` first when changing the logic.

- Three lyric inputs: `subtitles.ass` inside keyframes.zip / a standalone ASS / plain text. For `.txt`, `subs_from_txt()` builds an ASS with the default Karaoke style (Arial 64px, yellow highlight, PlayRes 1920x1080, the same values as the existing subtitles.ass) and it follows the same flow. Line breaks become the subtitle lines.
- **Strict character matching**: if the lyric character count without punctuation and symbols (`PUNCT_PATTERN`) differs from what Whisper detected, it stops. It is designed to fail when the lyrics differ from what was sung (ad-libs, skipped repeats). The error names the first line where they part ways (`describe_mismatch`); the totals alone left the whole lyric to search.
- The command line is a thin wrapper over `align()`, which `beatmv align` calls when a hybrid song has no `lyrics.whisper.ass`.
- Punctuation is folded, time included, into the `\k` of the character before. For the first line only, if the input ASS starts 0–3 s earlier than Whisper, the input start is kept (the sung entry). Gaps between lines are extended to just before the next line (repeated singing), and line ends that overlap the next line because of the `TAIL_MS` tail are cut at the next line's start (`trim_overlaps`). burn_subs.py draws only one line (the earlier wins), so overlaps do not show there, but passing this ASS straight to libass (the sister project's narration video template, `~/test/re6x`) would stack two lines.
- A punctuation-only line has no characters to match, so it gets no timing and keeps the input ASS times (placeholders for txt input). It warns rather than stops, so callers should not create punctuation-only lines.

### burn_subs.py — karaoke burning with diff rendering

Parses `\k` tags and draws a frame **only when the subtitle state changes**, joining them with ffmpeg concat. The state key is (background image, subtitle event, highlighted characters); frames with the same state are reused from the cache. Font size, margins and outline scale with the ratio of the ASS `PlayResY` to the real image resolution. The style named `Karaoke` is preferred.

Building a `RenderConfig` from the ASS style plus `--style-file` lives in `build_render_config()`, which `burn_subs_video.py` also uses (passing its mode as `force_mode`, line unless karaoke is asked for). Add new style options there and both pick them up.

`--style-file` (JSON, presets in `styles/`; `karaoke_blue` doubles as beatmv's default palette) overrides the drawing style. `mode: "line"` shows one line without karaoke highlighting and does not collect per-character transitions, so the state count drops sharply (223 → 33 on the sample). `position` / `letter_spacing` / `box` apply in both modes. Follow the existing rule that every px value is given for PlayResY and scaled to the real resolution. Invalid values warn and fall back (the video itself never fails because of them).

Line mode's `render_line_frame` adjusts lines that do not fit (`fit_line_layout`: shrink on one line down to `LINE_SHRINK_FLOOR`, otherwise wrap into two lines with `best_split`; English splits only at spaces, Japanese at the best-balanced character boundary).

**Both modes draw through `draw_text_block`** (lines of (text, colour) runs: one run per line in line mode, one per `\k` segment in karaoke). Placement, letter spacing and the box live only there; karaoke used to have its own bottom-only copy. Each run starts at the advance of the text before it, not at the sum of the runs' own widths: Hiragino kerns kana pairs too (ト・, え/い), and the old sum drifted 3–6 px by the end of a 64 px line. Given a transparent RGBA layer it keeps the transparency (for burn_subs_video.py; it relies on the box drawing keeping the mode). `render_event` draws onto any image; `render_frame` is that on a background still.

Fonts are searched in `font_candidates()` order: `$BURN_SUBS_FONT` if set, then `FONT_CANDIDATES` (Hiragino Kaku Gothic W7 first, Linux Noto CJK paths last so the sister service can import the module). make_cover.py imports `load_font()` so the subtitles and covers share a font.

**Note**: the output MP4 has an unusual structure (one frame lasts seconds, a keyframe only at the start), so extracting a frame with `ffmpeg -ss` can pick another frame and look like "no subtitles". Use frame numbers with `-vf "select=eq(n\,N)" -vsync 0` when checking.

Karaoke mode's (default) `render_frame` adjusts the same way with `fit_karaoke_layout` / `best_split_segs`. The only difference from line mode is that `\k` segments are the split units (never split inside one); the shrink-or-wrap decision is in `fit_units` and the split position in `best_split_index` (the unit is a character or a `\k` segment). Fixing only one mode would make the two disagree, so fix this core. Portrait videos (Shorts) wrap more often than landscape.

### burn_subs_video.py — burning subtitles onto a finished video

Puts subtitles on a live-action music video (picture and camera work kept) instead of a slideshow. It reuses burn_subs' diff rendering: `build_overlay_track` calls `build_concat_lines` with one transparent RGBA layer as the "background" (handed over through `img_cache`, nothing read from disk), so each subtitle state becomes a transparent PNG and the concat file is one transparent track, laid over the video with a single `overlay=eof_action=pass` (audio copied). The earlier design chained one `overlay` + `enable='between(...)'` per line, which is why it was line mode only; with one track, karaoke works too (`--karaoke` or `mode: "karaoke"`). Line mode stays the default here, unlike burn_subs.py. `eof_action=pass` matters: the default repeated the track's last frame and added a frame to the output. Events are sorted by start so `event_at` lets the earlier line win (`build_windows` states the same rule and is kept for the line count and tests). The concat demuxer puts PNG frames on a 1/25 s grid, so a subtitle can change up to one video frame off its ASS time, as in burn_subs.py.

### make_cover.py — cover images

Extracts an MP4 frame (the first by default, any time with `--at SEC`) with ffmpeg and draws the title and artist name with Pillow. `-ss` is on the output side (burn_subs.py output has a keyframe only at the start, so input-side seeking picks another frame). Without a title it uses **the MP4's parent folder name** (videos are kept in a folder named after the song). The default artist name is `Digital Armor Style`. Long titles shrink until they fit 92 % of the width. The Shorts version centres the 16:9 frame over a blurred, darkened background. It imports `burn_subs`, so run it with `.venv` too.

### beatmv/ — a music video drawn from the audio, no image assets

A different approach from the keyframes.zip slideshow. It analyses a song (made with Suno etc.) and draws, frame by frame with Pillow, a kick ring, percussion lines, a hit timeline and lyrics filled per `\k`, piping frames to FFmpeg. Entry point: `python -m beatmv {analyze,align,render,srt,thumb-bg} songs/<slug>`.

- **Shorts are `render --portrait --from A --to B`, not a script per song.** Per-song `build_short.py` copies in `work/` had drifted from render.py (no ghost stars, bass band, translation or Latin typeface). Positions per aspect ratio live in the `Layout` dataclass (`LANDSCAPE`, `PORTRAIT`); `PORTRAIT`'s values are those of Overclock's Short. Add a layout field rather than branching on portrait in the drawing code. A clip fades over `CLIP_FADE_IN` / `CLIP_FADE_OUT` unless its end is the song's.
- **A lyric still wider than the frame at `LYRIC_FLOOR` wraps into two rows** (`line_images` returns rows with their token ranges; `draw_lyrics` fills them one after the other). The split is burn_subs' `best_split_index`. No real song wraps in either layout yet (checked five in portrait), so this is covered by the tests' made-up long line only. The lyric block (lyric rows + translation rows) never goes below `Ctx.TEXT_FLOOR`, 8 px above the timeline's row labels: `draw_lyrics` moves the block up by the overlap. The limit is not the playhead's top because single-row lines with a translation already end up to 13 px below it in landscape; with this limit no line of any current song moves (checked all songs, both layouts), and the closest is 2 px away (new_morning, collision, landscape).

- **Per-song differences go only in `songs/<slug>/song.toml`** (title, audio, work directory, section times, `drum_rule`, `lyrics_aligner`). Copying scripts per song made changes like 4K support land in only one copy (it happened before the move). Keep `beats.json` and `lyrics.ass` in the same directory. **`songs/` is not tracked by git** (no lyrics or song data in a public repository). `songs/` and `work/` are symlinks to another local repository, where song settings, scripts and texts are committed (audio, video and images are not committed there either; the location is in local notes). Tests use the made-up song in `tests/fixtures/song/` and never read real songs.
- **Take times from the finished audio, not the recipe.** Suno does not keep the section lengths and adds interludes nobody asked for (11 s on 境界の調律者). Write values from Suno's alignment and analysis into `sections` etc.
- **Write coordinates, widths and text sizes in design px (1920x1080 landscape, 1080x1920 portrait) and pass them through `Ctx.px()`.** The default scale is 2 for landscape (4K) and 1 for portrait. Raw numbers break at 4K.
- **`drum_rule` is rock / edm / trance / dnb.** Picking peaks per band made the snare band pick up the kick's attack, so both rows became the same hits. Onsets are taken first and assigned by the rise of the low band versus the snare band. A four-on-the-floor kick's attack reaches 1–4 kHz, so EDM needs a different rule (the `drums.classify` tests have both examples). With edm, kick hits also meet the percussion condition and about half of PERC sits on a kick (evenly over beats 1–4, so not claps); trance removes them from percussion. dnb (drum and bass) takes the rock rule, removes hits that became kicks from the snare row, and removes weak-attack ghost notes and hats as well, keeping them as `ghost` in drums.json and drawing them as small light-blue stars on the snare row (`GHOST_STAR_R`, radius 11; they do not light the centre line. Dropping them was criticised as "playing but not shown"). With rock, a DnB kick's attack also lit as a downbeat snare and the breakbeat's small hits made the snare row busy.
- **Only show as hits what has been verified against the beat grid.** Before adding visuals, compare detected times with the beats in `beats.json`: the offset (within 40 ms?) and the misses (on what share of beats in the playing sections?). On アクセルを踏み抜け, bass hits were on time but only 20–80 % were found, and splitting claps from hats did not land on beats 2 and 4, so neither is drawn as hits. Bass is drawn as loudness (`bass_band`, `drums.write_bass_rms`, Demucs 4-stem split in `sep4/`).
- **With `lyrics_aligner = "whisper"` (an ASS from align_subtitles.py), the first character's `\k` holds the previous line's tail**, so a long one starts filling just before it is sung (`lyrics.load_lines`). SRT uses the same times.
- **For songs sung from kana, set `lyrics_aligner = "hybrid"` and build lyrics.ass with `python -m beatmv align songs/<slug>`** (`beatmv/suno_align.py`). If `lyrics.whisper.ass` is missing, it runs `align_subtitles.align()` itself on `work/<slug>/lyrics.txt` (or `--lyrics`) against the separated vocal when `sep4/` has one, else the song's audio; it never starts Demucs. Line starts and ends come from Suno's alignment (`work/<slug>/suno_alignment.json`, the saved MCP audio-lyrics-alignment response); character timing inside a line comes from Whisper (`work/<slug>/lyrics.whisper.ass`, align_subtitles.py output). Suno places lines accurately but returns kana; Whisper times kanji but drifts at line ends (2–4 s at the end of Overclock). Whisper's first character is trimmed to 0.4 s and its last to 1 s before mapping onto Suno's span. On Overclock this matched the hand-made subtitles except two hand-fixed lines. If line counts differ it stops rather than guess; holds over 1.5 s and line starts more than 2 s apart are listed for checking by ear and fixing by hand.
- **For songs sung in English, set `lyrics_aligner = "suno"` and write lyrics.ass from Suno's word timings with `beatmv align`** (`suno_align.words_to_ass`). No Whisper. A gap between words over `GAP_TOKEN` (0.15 s) gets an empty `\k`; otherwise the next word fills during the gap, before it is sung. A word spanning a newline (`s\nColor`) is split between the previous and the next line. Punctuation Suno attaches to the next word (`! Go`) is moved back onto the word before (`_LEADING_PUNCT`); otherwise the `!` stays unfilled until the next word. Suno's alignment can also break (the last two lines squeezed into 1 s, a word held for 12 s), so compare it with `vocal_rms.npy` and fix `suno_alignment.json` before aligning.
- **A translation (`translation`) must have one row per subtitle line; differing counts stop it** (`song.load_translation`), because a one-row offset gives every line another line's translation. The translation is shown under the lyric without filling, and the next-line preview is hidden (three rows leave the viewer unsure what to read). It is subtitles only, so Japanese translations use more kanji for readability.
- **Latin-only lines use a Latin typeface** (`font_for`, `EN_FONT`, default Helvetica Neue scaled ×1.18 to keep Hiragino's line width; `BEATMV_EN_FONT=sf|hiragino`). Hiragino's Latin letters read as "Japanese-made" to people abroad. Lines containing Japanese stay in Hiragino. Without Hiragino, `jp_font_path` falls back to burn_subs' `font_candidates()` (one weight for all), and without the Latin typeface Latin lines use the Japanese font.
- **Choose the waveform amplitude with `wave` (`mix` by default, `vocals` for the voice only).** Every other element is tied to one sound (ring = kick, lines = snare, band = bass), so with `vocals` the waveform is the voice and every element on screen maps to one sound. On Overclock, voice-only amplitude in the intro, interludes and outro was 0.0–0.03 (0.48–0.90 with `mix`), moving only while singing. analyze writes `vocal_rms.npy` from the Demucs 4-stem split (`sep4/`). `mix` stays the default so rebuilt existing songs do not change silently. While a hook line is sung, the waveform colour also moves to `hook_color` over `HOOK_FADE` (0.5 s), without flashing.
- **Hook lines (English lines of up to four words without Japanese, `lyrics.is_hook_line`) use larger text (`HOOK_FONT`) and fill with `hook_color` (cyan by default)**, because the Suno prompts open each chorus with a shout such as `Override! Override!`. They stand out by size and colour only, no flashing or beat zoom.
- **Colours come from `styles/<name>.json`, shared with burn_subs' `--style-file`** (`beatmv/style.py`), so slideshow videos and beatmv videos share a palette. beatmv reads colours only, never sizes (`font_size`, `outline`). beatmv-only keys (`accent_color`, `background_top`) pass silently through `load_style_file`, so burn_subs is unaffected. burn_subs warns about and drops invalid colours; beatmv raises, so a palette never changes silently.
- Visual constraints: no full-screen flashes, pulsing brightness or beat zoom (they read as flicker, per user feedback).
- Audio, separated stems, drums.json, rms.npy and videos live in `work/` (not tracked). Demucs randomly shifts its input by default (`--shifts 1`), so reruns vary; do not separate again if it is already done.
- Text images that do not change per frame are cached per worker (`line_cache`, `trans_cache`, `next_cache`); fade them with `faded()`, which copies, never with `putalpha` on the cached image.
- macOS multiprocessing uses spawn, so workers call `configure()` again via `Pool(initializer=...)` (module state is not inherited).

### keyframes.zip layout

`cut_XX.png` (keyframe images) + `inputs.txt` (image names and durations in ffmpeg concat format) + `subtitles.ass`. See README.md for details.
