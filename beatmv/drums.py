"""ドラム stem からキックと打楽器の打点を検出し、波形用の音量も書き出す。

Demucs でドラムとそれ以外に分け、ドラム全体のオンセットを取ってから、打点ごとに「低域（40-120Hz）」と
「スネア帯（1-4kHz + 胴鳴り 150-250Hz）」の立ち上がりを比べて振り分ける。帯域ごとに別々にピークを
拾うと、スネア帯がキックのアタックも拾って両者がほぼ同じ打点列になった（境界の調律者で 540 / 558 回）。

振り分けの基準は曲のドラムで変わる（song.toml の drum_rule）:
- rock: 低域が中高域より立てばキック、スネア帯が低域の 0.6 倍を超えればスネア。境界の調律者のサビ1で
  キックが 1・3 拍、スネアが 2・4 拍に分かれた。
- edm: 四つ打ちのキックはアタックが 1-4kHz にも強く出る（立ち上がりの中央値 3.1〜3.5）ので、rock の基準
  ではキックが 84 回しか残らなかった（駆け抜けろ Midnight）。低域が立てばキックとし、中高域が低域を
  1.2 以上上回った打点を打楽器（クラップ・ハット・刻み）とする。スネアだけではないので表示は PERC。
- trance: edm と同じ基準で、キックになった打点は打楽器に数えない。edm ではキックのアタックが中高域の条件も
  満たし、夜明けの境界線で打楽器 258 回のうち 131 回がキックと同じ打点だった（1〜4 拍に均等なので
  2・4 拍のクラップではない。駆け抜けろ Midnight も 48%）。裏打ちのハットだけが打楽器に残り、
  表拍のリングと裏拍の縦線が交互に光る。キックと同時のクラップも落ちるので、それが鳴る曲は edm のまま。
- dnb: キックは rock と同じ。スネアは rock の条件から、キックになった打点を外し、残りのうちスネア帯の
  立ち上がりが弱い下位 DNB_GHOST_QUANTILE を落とす。DnB のキックはアタックが強く、rock では 1 拍目の
  キックが 1 曲あたり 43〜89 回スネアにも出た。ブレイクビーツのゴーストノートとハットも拾い、スネアが
  1 小節に 2.5〜3.4 回光った（Digital Armor Style の 6 曲、2026-11-03）。dnb では 1.3〜1.8 回になり、
  2・4 拍に乗る割合が 6 曲中 5 曲で上がった（Overclock は 57% → 61%、Armor Reboot は 51% → 67%）。
  Half-Time の曲は 3 拍目にスネアが来るので、2・4 拍の割合は上がらない（Signal Lost 36% → 45%）。
  落とした弱い打点は drums.json の ghost に残し、スネアの段に小さく薄い印で出す（画面中央の縦線は光らせない）。
"""
import json
import os
import subprocess
import sys

import numpy as np

SR = 44100
HOP = 256
RISE_THRESHOLD = 1.0  # 前後 3 フレームでの log 振幅の上昇
ROCK_SNARE_VS_LOW = 0.6
EDM_PERC_OVER_LOW = 1.2
DNB_GHOST_QUANTILE = 0.4  # dnb でスネア候補から落とす、立ち上がりの弱い側の割合


def classify(t, lr, mr, rule):
    """オンセット時刻 t と、低域 lr / スネア帯 mr の立ち上がりから (kick, perc) の時刻を返す。"""
    t, lr, mr = np.asarray(t), np.asarray(lr), np.asarray(mr)
    if rule == "rock":
        kick = t[(lr > RISE_THRESHOLD) & (lr > mr)]
        perc = t[(mr > RISE_THRESHOLD) & (mr > lr * ROCK_SNARE_VS_LOW)]
    elif rule in ("edm", "trance"):
        is_kick = lr > RISE_THRESHOLD
        is_perc = (mr > RISE_THRESHOLD) & (mr - lr > EDM_PERC_OVER_LOW)
        if rule == "trance":
            is_perc &= ~is_kick
        kick, perc = t[is_kick], t[is_perc]
    elif rule == "dnb":
        kick, perc, _ = dnb_split(t, lr, mr)
    else:
        raise ValueError(f"unknown drum rule: {rule!r}")
    return kick, perc


def dnb_split(t, lr, mr):
    """dnb の (kick, snare, ghost)。ghost はスネア候補のうち立ち上がりの弱い側（ゴーストノート・ハット）。

    ghost を捨てると「鳴っているのに表示されない」打点になった（2026-11-03 のユーザーの指摘）ので、
    スネアの段に小さく薄い印で出せるよう、落とした分も返す。
    """
    t, lr, mr = np.asarray(t), np.asarray(lr), np.asarray(mr)
    is_kick = (lr > RISE_THRESHOLD) & (lr > mr)
    cand = (mr > RISE_THRESHOLD) & (mr > lr * ROCK_SNARE_VS_LOW) & ~is_kick
    strong = cand.copy()
    if cand.any():
        strong &= mr >= np.quantile(mr[cand], DNB_GHOST_QUANTILE)
    return t[is_kick], t[strong], t[cand & ~strong]


def stem_dir(song):
    stem = os.path.splitext(os.path.basename(song.audio))[0]
    return song.work("sep", "htdemucs", stem)


def separate(song):
    """drums.wav / no_drums.wav を作る。分離済みなら何もしない。

    Demucs は既定で入力をランダムにずらして推論する（--shifts 1）ので、作り直すと打点の検出結果も変わる。
    """
    drums = os.path.join(stem_dir(song), "drums.wav")
    if not os.path.exists(drums):
        import torch

        # Demucs の既定は cuda か cpu で、Apple Silicon の GPU（mps）は指定しないと使われない
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        subprocess.run(
            [sys.executable, "-m", "demucs", "-n", "htdemucs", "--two-stems", "drums",
             "-d", device, "-o", song.work("sep"), song.audio],
            check=True,
        )
    return drums


def separate4(song, stem):
    """Demucs の 4 分割（drums / bass / other / vocals）の stem を返す。分離済みなら何もしない。"""
    path = song.work("sep4", "htdemucs", os.path.splitext(os.path.basename(song.audio))[0], f"{stem}.wav")
    if not os.path.exists(path):
        import torch

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        subprocess.run(
            [sys.executable, "-m", "demucs", "-n", "htdemucs", "-d", device, "-o", song.work("sep4"), song.audio],
            check=True,
        )
    return path


def separate_bass(song):
    """bass.wav を作る（Demucs の 4 分割）。分離済みなら何もしない。

    ベースは打点ではなく音量で描く。アクセルを踏み抜けで 1 音ずつ拾おうとしたところ、時刻は 8 分音符の
    位置から 40 ms 以内に収まった（97%）が、裏打ちの音が続くと立ち上がりが立たず、鳴っている音の一部しか
    取れなかった（20 秒で 54 回ほどのところ 10〜41 回）。音量なら取りこぼしが起きない。
    """
    return separate4(song, "bass")


def write_bass_rms(song, fps=30):
    """帯の長さに使う、ベースの RMS を 1 コマ 1 値で bass_rms.npy に書く。"""
    import librosa

    y, sr = librosa.load(separate_bass(song), sr=22050, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=sr // fps)[0]
    rms = np.convolve(rms, np.ones(3) / 3, mode="same")  # 0.1 秒でならす（裏打ちの刻みは残す）
    np.save(song.work("bass_rms.npy"), np.clip(rms / np.percentile(rms, 98), 0, 1))


def write_vocal_rms(song, fps=30):
    """波形の振れ幅に使う、声だけの RMS を 1 コマ 1 値で vocal_rms.npy に書く（wave = "vocals"）。

    リング＝キック、縦線＝スネア、帯＝ベースと、ほかの要素は 1 つの音に結びついているのに、波形だけが
    声と伴奏の混ざった音（no_drums）で動いていた。声だけにすると、歌っている間だけ大きく動き、間奏では
    静まる。ならしは rms.npy と同じ 0.3 秒で、言葉ごとに跳ねないようにする。
    """
    import librosa

    y, sr = librosa.load(separate4(song, "vocals"), sr=22050, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=sr // fps)[0]
    rms = np.convolve(rms, np.ones(9) / 9, mode="same")
    np.save(song.work("vocal_rms.npy"), np.clip(rms / np.percentile(rms, 98), 0, 1))


def onset_rises(drums_wav):
    import librosa

    y, _ = librosa.load(drums_wav, sr=SR, mono=True)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)

    def band(lo, hi):
        return np.log1p(S[(freqs >= lo) & (freqs < hi)].sum(axis=0))

    def rise(e, i):
        return e[min(i + 3, len(e) - 1)] - e[max(i - 3, 0)]

    low, mid, body = band(40, 120), band(1000, 4000), band(150, 250)
    onsets = librosa.onset.onset_detect(
        y=y, sr=SR, hop_length=HOP, delta=0.08, wait=int(0.1 * SR / HOP))
    lr = np.array([rise(low, i) for i in onsets])
    mr = np.array([rise(mid, i) + 0.5 * rise(body, i) for i in onsets])
    return librosa.frames_to_time(onsets, sr=SR, hop_length=HOP), lr, mr


def write_rms(song, fps=30):
    """波形の振れ幅に使う、ドラム以外（声と伴奏）の RMS を 1 コマ 1 値で rms.npy に書く。"""
    import librosa

    y, sr = librosa.load(os.path.join(stem_dir(song), "no_drums.wav"), sr=22050, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=sr // fps)[0]
    rms = np.convolve(rms, np.ones(9) / 9, mode="same")  # 0.3 秒でならす
    np.save(song.work("rms.npy"), rms / np.percentile(rms, 98))


def analyze(song):
    os.makedirs(song.work_dir, exist_ok=True)
    drums = separate(song)
    t, lr, mr = onset_rises(drums)
    kick, perc = classify(t, lr, mr, song.drum_rule)
    # キー名は互換のため snare のまま（edm では打楽器全般）
    out = {"kick": [round(float(x), 3) for x in kick],
           "snare": [round(float(x), 3) for x in perc]}
    if song.drum_rule == "dnb":
        out["ghost"] = [round(float(x), 3) for x in dnb_split(t, lr, mr)[2]]
    with open(song.work("drums.json"), "w") as f:
        json.dump(out, f)
    write_rms(song)
    if song.bass_band:
        write_bass_rms(song)
    if song.wave == "vocals":
        write_vocal_rms(song)
    print(f"kick {len(kick)}  {song.perc_label.lower()} {len(perc)}  ({song.drum_rule})")
