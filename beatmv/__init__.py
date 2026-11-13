"""beatmv: analyse a song's audio and draw a motion-graphics music video, driven by beats and lyrics, with Pillow.

A different approach from the keyframes.zip slideshow (burn_subs.py): no image assets.
Per-song differences live in songs/<slug>/song.toml; the code is shared by every song.

    .venv/bin/python -m beatmv analyze songs/<slug>   # drum separation → kick/percussion hits and loudness
    .venv/bin/python -m beatmv rolls songs/<slug>     # list kick runs that may be snare rolls (from drums.json)
    .venv/bin/python -m beatmv align songs/<slug>     # lyrics.ass from Suno's (and Whisper's) alignment
    .venv/bin/python -m beatmv render songs/<slug>    # 4K video (--scale 1 for 1080p, --portrait for a Short,
                                                      # --from / --to for a clip, --preview sec,... for stills)
    .venv/bin/python -m beatmv srt songs/<slug>       # YouTube subtitles (SRT)
    .venv/bin/python -m beatmv thumb-bg songs/<slug>  # thumbnail background (no text; --portrait for a Short's
                                                      # cover; the title is added by the composing pipeline)
"""
