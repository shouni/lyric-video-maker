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


def classify(t, lr, mr, rule):
    """オンセット時刻 t と、低域 lr / スネア帯 mr の立ち上がりから (kick, perc) の時刻を返す。"""
    t, lr, mr = np.asarray(t), np.asarray(lr), np.asarray(mr)
    if rule == "rock":
        kick = t[(lr > RISE_THRESHOLD) & (lr > mr)]
        perc = t[(mr > RISE_THRESHOLD) & (mr > lr * ROCK_SNARE_VS_LOW)]
    elif rule == "edm":
        kick = t[lr > RISE_THRESHOLD]
        perc = t[(mr > RISE_THRESHOLD) & (mr - lr > EDM_PERC_OVER_LOW)]
    else:
        raise ValueError(f"unknown drum rule: {rule!r}")
    return kick, perc


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
    with open(song.work("drums.json"), "w") as f:
        json.dump(out, f)
    write_rms(song)
    print(f"kick {len(kick)}  {song.perc_label.lower()} {len(perc)}  ({song.drum_rule})")
