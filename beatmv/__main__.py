"""beatmv の入口。サブコマンドは曲のディレクトリ（songs/<slug>）を受け取る。"""
import argparse

from beatmv import drums, lyrics, render, song as songmod, thumbnail


def main():
    ap = argparse.ArgumentParser(prog="beatmv")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="ドラムを分離し、キック/打楽器の打点と波形用の音量を書き出す")
    a.add_argument("song_dir")

    r = sub.add_parser("render", help="MV を書き出す（既定 4K）")
    r.add_argument("song_dir")
    r.add_argument("--scale", type=int, default=2, help="1920x1080 に対する倍率（1=1080p, 2=4K）")
    r.add_argument("--preview", help="この時刻（秒, カンマ区切り）の静止画だけ書き出す")

    s = sub.add_parser("srt", help="YouTube 字幕用の SRT を書き出す")
    s.add_argument("song_dir")

    t = sub.add_parser("thumb-bg", help="サムネイルの背景（1280x720、文字なし）を書き出す")
    t.add_argument("song_dir")

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
        subs = lyrics.to_srt(lyrics.load_lines(song.lyrics, song.lyrics_aligner), out)
        print(f"{out}  {len(subs)} lines")
    elif args.cmd == "thumb-bg":
        out = song.work("thumb_bg.png")
        at = thumbnail.background(args.song_dir, out)
        print(f"{out}  t={at:.2f}")


if __name__ == "__main__":
    main()
