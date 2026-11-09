"""Detect kick and percussion hits in the drum stem, and write loudness for the waveform.

Demucs splits drums from the rest. Onsets are taken from the whole drum stem first, then each hit is
assigned by comparing the rise in the low band (40–120 Hz) with the rise in the snare band
(1–4 kHz plus the 150–250 Hz body). Picking peaks per band separately made the snare band pick up the
kick's attack too, so both rows came out almost identical (540 / 558 hits on 境界の調律者).

The rule depends on the song's drums (drum_rule in song.toml):
- rock: a kick when the low band rises more than the mids, a snare when the snare band exceeds 0.6× the
  low band. On 境界の調律者's first chorus this split kicks onto beats 1 and 3 and snares onto 2 and 4.
- edm: a four-on-the-floor kick's attack is strong in 1–4 kHz too (median rise 3.1–3.5), so the rock rule
  kept only 84 kicks (駆け抜けろ Midnight). A rising low band makes a kick; hits where the mids exceed the
  low band by more than 1.2 are percussion (claps, hats, ticks). Not only snares, so it is labelled PERC.
- trance: the edm rule, but hits that became kicks are not counted as percussion. With edm the kick's
  attack also meets the mid condition: on 夜明けの境界線, 131 of 258 percussion hits sat on a kick (spread
  evenly over beats 1–4, so not claps on 2 and 4; 駆け抜けろ Midnight had 48 %). Only the off-beat hats
  remain, so the ring (on-beat) and the vertical lines (off-beat) alternate. Claps on the kick are lost too,
  so songs that have them stay on edm.
- dnb: kicks as in rock. Snares use the rock condition minus the hits that became kicks, then drop the
  weakest DNB_GHOST_QUANTILE of the snare-band rises. DnB kicks have a hard attack, and with rock the
  downbeat kick also showed as a snare 43–89 times per song. Breakbeat ghost notes and hats were picked up
  too, so the snare lit 2.5–3.4 times per bar (six Digital Armor Style songs, 2026-11-03). With dnb it is
  1.3–1.8, and the share on beats 2 and 4 rose in 5 of 6 songs (Overclock 57 % → 61 %, Armor Reboot
  51 % → 67 %). Half-time songs put the snare on beat 3, so their share does not rise (Signal Lost
  36 % → 45 %). The dropped weak hits are kept as `ghost` in drums.json and drawn small on the snare row
  (they do not light the centre line).
"""
import json
import os
import subprocess
import sys

import numpy as np

SR = 44100
HOP = 256
RISE_THRESHOLD = 1.0  # rise in log magnitude across ±3 frames
ROCK_SNARE_VS_LOW = 0.6
EDM_PERC_OVER_LOW = 1.2
DNB_GHOST_QUANTILE = 0.4  # share of the weakest snare candidates that dnb drops


def classify(t, lr, mr, rule):
    """Return (kick, perc) times from onset times t and the rises of the low band lr / snare band mr."""
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
    """(kick, snare, ghost) for dnb. ghost is the weak side of the snare candidates (ghost notes, hats).

    Throwing ghost away left hits that "play but do not show" (pointed out on 2026-11-03), so the dropped
    hits are returned too, to be drawn small on the snare row.
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
    """Create drums.wav / no_drums.wav. Does nothing if already separated.

    Demucs randomly shifts its input by default (--shifts 1), so separating again also changes the detected hits.
    """
    drums = os.path.join(stem_dir(song), "drums.wav")
    if not os.path.exists(drums):
        import torch

        # Demucs defaults to cuda or cpu; the Apple Silicon GPU (mps) is only used when asked for
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        subprocess.run(
            [sys.executable, "-m", "demucs", "-n", "htdemucs", "--two-stems", "drums",
             "-d", device, "-o", song.work("sep"), song.audio],
            check=True,
        )
    return drums


def separate4(song, stem):
    """Return a stem of the Demucs 4-stem split (drums / bass / other / vocals). Does nothing if already separated."""
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
    """Create bass.wav (Demucs 4-stem split). Does nothing if already separated.

    Bass is drawn as loudness, not hits. Picking single notes on アクセルを踏み抜け put them within 40 ms of the
    eighth-note grid (97 %), but a running off-beat line has weak attacks and only part of it was found
    (10–41 of about 54 notes in 20 s). Loudness misses nothing.
    """
    return separate4(song, "bass")


def write_bass_rms(song, fps=30):
    """Write the bass RMS, one value per frame, to bass_rms.npy (it sets the band length)."""
    import librosa

    y, sr = librosa.load(separate_bass(song), sr=22050, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=sr // fps)[0]
    rms = np.convolve(rms, np.ones(3) / 3, mode="same")  # smooth over 0.1 s (keeps the off-beat pulse)
    np.save(song.work("bass_rms.npy"), np.clip(rms / np.percentile(rms, 98), 0, 1))


def write_vocal_rms(song, fps=30):
    """Write the vocal-only RMS, one value per frame, to vocal_rms.npy (wave = "vocals").

    Every other element is tied to one sound (ring = kick, lines = snare, band = bass), but the waveform moved
    with voice and backing mixed (no_drums). With the voice only, it moves while singing and calms in the
    interludes. Smoothed over 0.3 s like rms.npy so it does not jump on every word.
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
    """Write the RMS of everything but drums (voice and backing), one value per frame, to rms.npy."""
    import librosa

    y, sr = librosa.load(os.path.join(stem_dir(song), "no_drums.wav"), sr=22050, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=sr // fps)[0]
    rms = np.convolve(rms, np.ones(9) / 9, mode="same")  # smooth over 0.3 s
    np.save(song.work("rms.npy"), rms / np.percentile(rms, 98))


def analyze(song):
    os.makedirs(song.work_dir, exist_ok=True)
    drums = separate(song)
    t, lr, mr = onset_rises(drums)
    kick, perc = classify(t, lr, mr, song.drum_rule)
    # the key stays "snare" for compatibility (general percussion under edm)
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
