"""Offline modal impact synthesis (the web viewer runs the same model live)."""
import numpy as np
from scipy.io import wavfile


def contact_spectrum(freqs, tau):
    """|F(f)| / |F(0)| of a half-sine hammer pulse lasting tau seconds."""
    x = 2 * freqs * tau
    with np.errstate(divide="ignore", invalid="ignore"):
        h = np.abs(np.cos(np.pi * freqs * tau) / (1 - x ** 2))
    return np.where(np.isclose(x, 1), np.pi / 4, h)


def strike_amplitudes(phi_v, normal, freqs, tau=4e-4):
    """phi_v: (3, m) mode shapes at the hit vertex; normal: (3,)."""
    return (normal @ phi_v) * contact_spectrum(freqs, tau)


def render(freqs, decays, amps, sr=44100, max_dur=4.0, seed=0):
    keep = (freqs > 20) & (freqs < 0.45 * sr) & (np.abs(amps) > 1e-3 * np.abs(amps).max())
    f, d, a = freqs[keep], decays[keep], amps[keep]
    dur = float(np.clip((6.9 / d).max(), 0.3, max_dur)) if keep.any() else 0.3  # until -60 dB
    t = np.arange(int(sr * dur)) / sr
    y = (a[:, None] * np.exp(-d[:, None] * t) * np.sin(2 * np.pi * f[:, None] * t)).sum(axis=0)
    # A few ms of contact noise makes the attack read as a hit rather than a tone. Tiny
    # objects (a 4.5 cm netsuke rings above 15 kHz) are nothing but this click.
    n = int(0.003 * sr)
    click = np.random.default_rng(seed).standard_normal(n) * np.exp(-np.arange(n) / (0.0008 * sr))
    y[:n] += click * (0.03 * np.abs(y).max() if keep.any() else 1.0)
    return y / (np.abs(y).max() + 1e-12)


def fade_out(y, sr, seconds=0.05):
    n = min(len(y), int(sr * seconds))
    y = y.copy()
    y[-n:] *= np.linspace(1, 0, n)
    return y


def sequence(clips, onsets, sr=44100):
    """Mix clips at onset times (s) into one track."""
    end = max(o + len(c) / sr for c, o in zip(clips, onsets))
    out = np.zeros(int(end * sr) + 1)
    for c, o in zip(clips, onsets):
        i = int(o * sr)
        out[i:i + len(c)] += c
    return out / (np.abs(out).max() + 1e-12)


def write_wav(path, y, sr=44100):
    wavfile.write(path, sr, (np.clip(y, -1, 1) * 0.9 * 32767).astype(np.int16))
