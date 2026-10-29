"""Синтез коротких звуков оформления: вжух на склейке, щелчок, поп.

Все функции пишут стерео WAV float32 (48 кГц), пик −6 дБ, и возвращают путь к файлу.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import scipy.io.wavfile as wavfile
from scipy.signal import butter, lfilter

SR = 48000


def _write(path, x: np.ndarray, sr: int, peak: float = .5) -> Path:
    x = np.asarray(x, dtype=np.float64)
    m = np.abs(x).max()
    if m > 0:
        x = x / m * peak
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sr, np.stack([x, x], 1).astype(np.float32))
    return path


def whoosh(path, sr: int = SR, dur: float = .38, seed: int = 7) -> Path:
    """Короткий «вжух» для склейки: шум с полосой частот около 1,8 кГц и мягкой огибающей."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    t = np.arange(n) / sr
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    spec *= np.exp(-((freqs - 1800) / 1400) ** 2) + .15 * np.exp(-((freqs - 500) / 400) ** 2)
    x = np.fft.irfft(spec, n)
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2 * np.exp(-t * 2)
    return _write(path, x * env, sr)


def click(path, sr: int = SR, seed: int = 3) -> Path:
    """Щелчок замка: два коротких транзиента (язычок + упор) — фильтрованный шум и тон с быстрым затуханием."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(.35 * sr))

    def tick(t0, f, amp, dec):
        i, m = int(t0 * sr), int(.06 * sr)
        t = np.arange(m) / sr
        b, a = butter(2, [f * .6 / (sr / 2), min(f * 1.8 / (sr / 2), .99)], "band")
        noise = lfilter(b, a, rng.standard_normal(m))
        out[i:i + m] += amp * np.exp(-t / dec) * (.6 * noise + .4 * np.sin(2 * np.pi * f * t))

    tick(0.0, 2400, .5, .006)
    tick(0.055, 1300, .7, .012)
    return _write(path, out, sr)


def pop(path, sr: int = SR, dur: float = .12, seed: int = 5) -> Path:
    """Мягкий «поп» (появление элемента): тон с падением высоты 520→180 Гц, быстрая атака и спад."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    t = np.arange(n) / sr
    f = 180 + 340 * np.exp(-t / .018)
    phase = 2 * np.pi * np.cumsum(f) / sr
    env = (1 - np.exp(-t / .002)) * np.exp(-t / .03)
    b, a = butter(2, 3000 / (sr / 2))
    air = lfilter(b, a, rng.standard_normal(n)) * np.exp(-t / .006)
    return _write(path, env * np.sin(phase) + .15 * air, sr, peak=.45)


__all__ = ["whoosh", "click", "pop"]
