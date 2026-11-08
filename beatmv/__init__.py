"""beatmv: 曲の音を解析して、拍と歌詞で動くモーショングラフィックの MV を Pillow で描く。

keyframes.zip のスライドショー（burn_subs.py）とは別の作り方で、画像素材を使わない。
曲ごとの違いは songs/<slug>/song.toml に寄せ、コードは全曲で共有する。

    .venv/bin/python -m beatmv analyze songs/<slug>   # ドラム分離 → キック/打楽器の打点・音量
    .venv/bin/python -m beatmv align songs/<slug>     # Suno と Whisper の時刻合わせから lyrics.ass（hybrid）
    .venv/bin/python -m beatmv render songs/<slug>    # 4K の MV（--scale 1 で 1080p、--preview 秒,...）
    .venv/bin/python -m beatmv srt songs/<slug>       # YouTube 字幕（SRT）
    .venv/bin/python -m beatmv thumb-bg songs/<slug>  # サムネイルの背景（文字なし。曲名は作曲パイプライン側で載せる）
"""
