"""beatmv entry point. Every subcommand takes a song directory (songs/<slug>)."""
import argparse
import json
import os

from beatmv import drums, lyrics, render, song as songmod, suno_align, suno_kana, thumbnail


def main():
    ap = argparse.ArgumentParser(prog="beatmv")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze",
                       help="separate the drums and write kick/percussion hits and the loudness for the waveform")
    a.add_argument("song_dir")

    r = sub.add_parser("render", help="render the video (4K by default; --portrait for a 1080x1920 Short)")
    r.add_argument("song_dir")
    r.add_argument("--portrait", action="store_true", help="9:16 layout for Shorts (1080x1920 at scale 1)")
    r.add_argument("--scale", type=int,
                   help="scale relative to the layout's design size (default: 2 = 4K landscape, 1 for --portrait)")
    r.add_argument("--from", dest="start", type=float, metavar="SEC",
                   help="render only from this time (a clip for checking, or a Short)")
    r.add_argument("--to", dest="end", type=float, metavar="SEC", help="render only up to this time")
    r.add_argument("--preview", help="write only stills at these times (seconds, comma-separated; "
                                     "not with --from / --to)")

    s = sub.add_parser("srt", help="write SRT subtitles for YouTube")
    s.add_argument("song_dir")

    ro = sub.add_parser("rolls", help="list kick runs that may be snare rolls (from drums.json; no analysis)")
    ro.add_argument("song_dir")

    t = sub.add_parser("thumb-bg", help="write the thumbnail background (1280x720, no text)")
    t.add_argument("song_dir")
    t.add_argument("--portrait", action="store_true", help="the Short's cover instead (1080x1920)")

    al = sub.add_parser("align",
                        help="write lyrics.ass: suno uses Suno's alignment only, suno_kana maps Suno's kana onto "
                             "the display lyrics, hybrid combines Suno with Whisper")
    al.add_argument("song_dir")
    al.add_argument("--whisper", help="output of align_subtitles.py (default: work/<slug>/lyrics.whisper.ass; "
                                      "hybrid runs align_subtitles.py itself when the file is missing)")
    al.add_argument("--lyrics", help="display lyrics for suno_kana (txt, one line per sung line), or the lyrics for "
                                     "hybrid's Whisper run (txt or ASS) (default: work/<slug>/lyrics.txt)")
    al.add_argument("--model", default="large-v3", help="Whisper model for that run (default: large-v3)")
    al.add_argument("--language", default="ja", help="lyrics language for that run (default: ja)")
    al.add_argument("--suno", help="Suno's alignment JSON (default: work/<slug>/suno_alignment.json)")

    args = ap.parse_args()
    song = songmod.load(args.song_dir)

    if args.cmd == "analyze":
        drums.analyze(song)
    elif args.cmd == "render":
        scale = args.scale or (1 if args.portrait else 2)
        if args.preview and (args.start is not None or args.end is not None):
            ap.error("--preview writes stills at the times given; it cannot be combined with --from / --to")
        if args.preview:
            for p in render.preview(args.song_dir, scale, args.preview.split(","), args.portrait):
                print(p)
        else:
            try:
                clip = render.clip_span(song, args.start, args.end)
            except ValueError as exc:
                ap.error(str(exc))
            print(render.render_video(args.song_dir, scale, args.portrait, clip))
    elif args.cmd == "srt":
        out = song.work(f"{song.slug}.srt")
        lines = lyrics.load_lines(song.lyrics, song.lyrics_aligner)
        subs = lyrics.to_srt(lines, out)
        print(f"{out}  {len(subs)} lines")
        trans = songmod.load_translation(song, len(lines))
        if trans is not None:
            out = song.work(f"{song.slug}.ja.srt")
            subs = lyrics.to_srt([(tr or text, *rest) for tr, (text, *rest) in zip(trans, lines, strict=False)], out)
            print(f"{out}  {len(subs)} lines (translation)")
    elif args.cmd == "align" and song.lyrics_aligner == "suno":
        subs = suno_align.words_to_ass(suno_align.load_words(args.suno or song.work("suno_alignment.json")))
        subs.save(song.lyrics)
        print(f"{song.lyrics}  {len(subs)} lines (Suno word timings as they are)")
    elif args.cmd == "align" and song.lyrics_aligner == "suno_kana":
        lyrics_in = args.lyrics or song.work("lyrics.txt")
        if not os.path.exists(lyrics_in):
            raise SystemExit(f"Error: no display lyrics: {lyrics_in}\n"
                             "       Put the lyrics as shown (kanji) there, one line per sung line, or pass --lyrics.")
        with open(lyrics_in, encoding="utf-8") as f:
            display = f.read().splitlines()
        try:
            subs, warnings = suno_kana.lines_to_ass(
                display, suno_align.load_words(args.suno or song.work("suno_alignment.json")))
        except ValueError as exc:
            raise SystemExit(f"Error: {exc}") from None
        subs.save(song.lyrics)
        print(f"{song.lyrics}  {len(subs)} lines (Suno's kana timings on the display lyrics)")
        for w in warnings:
            print(f"  check: {w}")
    elif args.cmd == "align":
        whisper = args.whisper or song.work("lyrics.whisper.ass")
        suno = args.suno or song.work("suno_alignment.json")
        if not os.path.exists(whisper):
            run_whisper(song, whisper, args)
        subs, warnings = suno_align.merge(whisper, suno_align.load_words(suno))
        subs.save(song.lyrics)
        print(f"{song.lyrics}  {len(subs)} lines")
        for w in warnings:
            print(f"  check: {w}")
        if song.lyrics_aligner != "hybrid":
            print(f'  set lyrics_aligner to "hybrid" in song.toml (it is {song.lyrics_aligner!r})')
    elif args.cmd == "rolls":
        with open(song.work("drums.json")) as f:
            drums.print_roll_candidates(song, json.load(f)["kick"])
    elif args.cmd == "thumb-bg":
        out = song.work("thumb_bg_portrait.png" if args.portrait else "thumb_bg.png")
        at = thumbnail.background(args.song_dir, out, args.portrait)
        print(f"{out}  t={at:.2f}")


def run_whisper(song, out, args):
    """Run align_subtitles.py's alignment to make the Whisper ASS that hybrid needs (it used to be a manual step).

    The audio is the separated vocal when `analyze` has already made it (more accurate than the full mix),
    otherwise the song's audio; Demucs is not run here.
    """
    lyrics_in = args.lyrics or song.work("lyrics.txt")
    if not os.path.exists(lyrics_in):
        raise SystemExit(f"Error: {out} is missing and there are no lyrics to align: {lyrics_in}\n"
                         "       Put the lyrics there (one line per subtitle line) or pass --lyrics.")
    vocals = drums.stem4_path(song, "vocals")
    audio = vocals if os.path.exists(vocals) else song.audio
    print(f"{out} not found: aligning {lyrics_in} to {audio} with Whisper")
    import align_subtitles  # imported here: only this path needs it

    align_subtitles.align(audio, lyrics_in, out, model=args.model, language=args.language)


if __name__ == "__main__":
    main()
