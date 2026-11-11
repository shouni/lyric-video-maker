"""beatmv entry point. Every subcommand takes a song directory (songs/<slug>)."""
import argparse

from beatmv import drums, lyrics, render, song as songmod, suno_align, thumbnail


def main():
    ap = argparse.ArgumentParser(prog="beatmv")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze",
                       help="separate the drums and write kick/percussion hits and the loudness for the waveform")
    a.add_argument("song_dir")

    r = sub.add_parser("render", help="render the video (4K by default)")
    r.add_argument("song_dir")
    r.add_argument("--scale", type=int, default=2, help="scale relative to 1920x1080 (1 = 1080p, 2 = 4K)")
    r.add_argument("--preview", help="write only stills at these times (seconds, comma-separated)")

    s = sub.add_parser("srt", help="write SRT subtitles for YouTube")
    s.add_argument("song_dir")

    t = sub.add_parser("thumb-bg", help="write the thumbnail background (1280x720, no text)")
    t.add_argument("song_dir")

    al = sub.add_parser("align",
                        help="write lyrics.ass: suno uses Suno's alignment only, hybrid combines it with Whisper")
    al.add_argument("song_dir")
    al.add_argument("--whisper", help="output of align_subtitles.py (default: work/<slug>/lyrics.whisper.ass)")
    al.add_argument("--suno", help="Suno's alignment JSON (default: work/<slug>/suno_alignment.json)")

    args = ap.parse_args()
    song = songmod.load(args.song_dir)

    if args.cmd == "analyze":
        drums.analyze(song)
    elif args.cmd == "render":
        if args.preview:
            for p in render.preview(args.song_dir, args.scale, args.preview.split(",")):
                print(p)
        else:
            print(render.render_video(args.song_dir, args.scale))
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
    elif args.cmd == "align":
        whisper = args.whisper or song.work("lyrics.whisper.ass")
        suno = args.suno or song.work("suno_alignment.json")
        subs, warnings = suno_align.merge(whisper, suno_align.load_words(suno))
        subs.save(song.lyrics)
        print(f"{song.lyrics}  {len(subs)} lines")
        for w in warnings:
            print(f"  check: {w}")
        if song.lyrics_aligner != "hybrid":
            print(f'  set lyrics_aligner to "hybrid" in song.toml (it is {song.lyrics_aligner!r})')
    elif args.cmd == "thumb-bg":
        out = song.work("thumb_bg.png")
        at = thumbnail.background(args.song_dir, out)
        print(f"{out}  t={at:.2f}")


if __name__ == "__main__":
    main()
