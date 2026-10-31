"""beatmv: 曲の音を解析して、拍と歌詞で動くモーショングラフィックの MV を Pillow で描く。

keyframes.zip のスライドショー（burn_subs.py）とは別の作り方で、画像素材を使わない。
曲ごとの違いは songs/<slug>/song.toml に寄せ、コードは全曲で共有する。

    .venv/bin/python -m beatmv analyze songs/midnight   # ドラム分離 → キック/打楽器の打点・音量
    .venv/bin/python -m beatmv render songs/midnight    # 4K の MV（--scale 1 で 1080p、--preview 秒,...）
    .venv/bin/python -m beatmv srt songs/midnight       # YouTube 字幕（SRT）
    .venv/bin/python -m beatmv thumb-bg songs/midnight  # サムネイルの背景（文字は ap-music の ApplyTitle で載せる）
"""
