"""Синтез коротких звуков оформления: вжух на склейке, щелчок, поп, стук, удар «БАХ», штамп, свист.

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


def _thump(n: int, sr: int, f0: float, f1: float, dec: float, tau: float = .02) -> np.ndarray:
    """Низкий удар: синус с падением высоты f0→f1 и экспоненциальным спадом."""
    t = np.arange(n) / sr
    f = f1 + (f0 - f1) * np.exp(-t / tau)
    return (1 - np.exp(-t / .0015)) * np.exp(-t / dec) * np.sin(2 * np.pi * np.cumsum(f) / sr)


def _band(rng, n: int, sr: int, lo: float, hi: float) -> np.ndarray:
    b, a = butter(2, [lo / (sr / 2), min(hi / (sr / 2), .99)], "band")
    return lfilter(b, a, rng.standard_normal(n))


def knock(path, sr: int = SR, seed: int = 11) -> Path:
    """«Тук-тук»: два деревянных стука — короткий резонанс ~180 и ~620 Гц плюс щелчок по дереву."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(.5 * sr))
    for t0, amp in ((0.0, 1.0), (0.2, .85)):
        i, m = int(t0 * sr), int(.16 * sr)
        t = np.arange(m) / sr
        body = .8 * _thump(m, sr, 260, 170, .035) + .45 * np.sin(2 * np.pi * 620 * t) * np.exp(-t / .018)
        tap = _band(rng, m, sr, 900, 3500) * np.exp(-t / .004) * .6
        out[i:i + m] += amp * (body + tap)
    return _write(path, out, sr, peak=.6)


def bang(path, sr: int = SR, seed: int = 13) -> Path:
    """«БАХ»: плотный низкий удар (90→45 Гц) и короткая вспышка шума с хвостом."""
    rng = np.random.default_rng(seed)
    n = int(.7 * sr)
    t = np.arange(n) / sr
    low = _thump(n, sr, 110, 45, .16, tau=.03)
    b, a = butter(2, 4000 / (sr / 2))
    burst = lfilter(b, a, rng.standard_normal(n)) * (np.exp(-t / .03) + .15 * np.exp(-t / .2))
    x = np.tanh(1.6 * (low + .5 * burst))     # мягкое ограничение — «плотность» без перегруза
    return _write(path, x, sr, peak=.7)


def stamp(path, sr: int = SR, seed: int = 17) -> Path:
    """Штамп: глухой удар (140→70 Гц) и шлепок бумаги — шум 1–5 кГц с очень быстрым спадом."""
    rng = np.random.default_rng(seed)
    n = int(.4 * sr)
    t = np.arange(n) / sr
    thud = _thump(n, sr, 140, 70, .06)
    slap = _band(rng, n, sr, 1000, 5000) * np.exp(-t / .012)
    return _write(path, thud + .55 * slap, sr, peak=.6)


def swoosh(path, sr: int = SR, dur: float = .32, seed: int = 19) -> Path:
    """Быстрый воздушный свист: шум, полоса которого скользит 600 Гц → 5 кГц, огибающая с пиком в конце."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    t = np.arange(n) / sr
    noise = rng.standard_normal(n)
    x = np.zeros(n)
    seg = 256
    for k in range(0, n, seg):  # фильтр с меняющейся полосой — по кускам
        c = 600 * (5000 / 600) ** (k / n)
        b, a = butter(2, [c * .7 / (sr / 2), min(c * 1.4 / (sr / 2), .99)], "band")
        x[k:k + seg] = lfilter(b, a, noise[max(0, k - 2048):k + seg])[-len(x[k:k + seg]):]
    env = (t / dur) ** 1.5 * np.exp(-((t - .8 * dur) / (.25 * dur)) ** 2) + 1e-3
    env *= np.minimum(1, (dur - t) / .02)
    return _write(path, x * env, sr, peak=.5)


# звуки для reel.yaml: sfx: [{at, sound, volume}]
SOUNDS = {"knock": knock, "bang": bang, "pop": pop, "whoosh": whoosh, "stamp": stamp, "click": click,
          "swoosh": swoosh}


def duration(path) -> float:
    """Длительность WAV, с."""
    sr, x = wavfile.read(str(path))
    return round(len(x) / sr, 3)


def make(name: str, path) -> Path:
    """Пишет звук по имени из SOUNDS; неизвестное имя — ValueError."""
    if name not in SOUNDS:
        raise ValueError(f"неизвестный звук «{name}». Есть: {', '.join(sorted(SOUNDS))}")
    return SOUNDS[name](path)


__all__ = ["whoosh", "click", "pop", "knock", "bang", "stamp", "swoosh", "SOUNDS", "make", "duration"]
