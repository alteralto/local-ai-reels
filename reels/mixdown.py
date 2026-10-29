"""Музыка монтажа с приглушением под голос (ducking): своя дорожка music.wav на каждый монтаж.

Раньше музыка шла с постоянной громкостью data-volume≈0.24 под всем роликом. Здесь громкость следует раскладке
блоков монтажа (timing.place): под речью музыка тише на duck_db, в паузах, во вступлении и на финальной карточке —
громче (base_db). Переходы — плавные (приподнятый косинус): attack — заранее, до начала речи (раскладка известна
наперёд, поэтому к первому слову музыка уже приглушена), release — после конца речи. Огибающая считается numpy
по отсчётам 48 кГц — без ffmpeg-выражений, детерминированно. Трек подрезается (или повторяется с перекрёстным
затуханием) ровно до длительности монтажа; затухания в начале и в конце вшиваются в файл, поэтому в index.html
дорожка ставится с data-volume="1" и без data-fade-*.

Громкости — относительно music.wav (он уже −18 LUFS, см. music.normalize): base_db=-3 → ≈−21 LUFS в паузах,
base_db+duck_db=-15 → ≈−33 LUFS под речью (было постоянно 0.24 ≈ −12.4 дБ → ≈−30.4 LUFS).

Настройки в reel.yaml (предложение): music.duck (bool, по умолчанию true), music.duck_db (−12), music.duck_base_db (−3).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

import numpy as np

from . import util

SR = 48000
CHANNELS = 2
LOOP_XFADE = 1.0   # с, перекрёстное затухание на стыке, если трек короче монтажа


def speech_intervals(layout: Iterable, merge_gap: float = 0.0) -> list[tuple[float, float]]:
    """Интервалы речи монтажа [(start, end)] в секундах таймлайна, по возрастанию.

    layout — раскладка timing.place (у элемента есть .start и .length) или пары (start, end).
    Пересекающиеся интервалы и интервалы с паузой ≤ merge_gap между ними сливаются; пустые — отбрасываются.
    """
    iv = []
    for p in layout:
        if hasattr(p, "start"):
            s, e = float(p.start), float(p.start) + float(p.length)
        else:
            s, e = (float(x) for x in p)
        if e > s:
            iv.append((s, e))
    iv.sort()
    out: list[tuple[float, float]] = []
    for s, e in iv:
        if out and s - out[-1][1] <= merge_gap + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return [(round(s, 6), round(e, 6)) for s, e in out]


def _rise(n: int) -> np.ndarray:
    """Плавный подъём 0→1 на n отсчётах (приподнятый косинус, без концевых 0 и 1)."""
    if n <= 0:
        return np.empty(0)
    x = (np.arange(n) + 0.5) / n
    return 0.5 - 0.5 * np.cos(np.pi * x)


def _put_max(d: np.ndarray, lo: int, vals: np.ndarray) -> None:
    """d[lo:lo+len(vals)] = max(d, vals) с обрезкой по краям массива."""
    hi = lo + len(vals)
    a, b = max(lo, 0), min(hi, len(d))
    if a < b:
        np.maximum(d[a:b], vals[a - lo:b - lo], out=d[a:b])


def duck_depth(intervals: list[tuple[float, float]], n: int, sr: int = SR, attack: float = 0.12,
               release: float = 0.35) -> np.ndarray:
    """Глубина приглушения по отсчётам: 1 — под речью, 0 — вне её; плавные переходы attack (до начала речи)
    и release (после конца). Соседние переходы не складываются — берётся максимум (короткая пауза
    между блоками даёт лишь небольшой подъём, а не «качание»)."""
    d = np.zeros(n, dtype=np.float64)
    a, r = max(int(round(attack * sr)), 0), max(int(round(release * sr)), 0)
    up, down = _rise(a), _rise(r)[::-1]
    for s, e in intervals:
        i0, i1 = int(round(s * sr)), int(round(e * sr))
        _put_max(d, i0 - a, up)
        if i1 > i0:
            _put_max(d, i0, np.ones(i1 - i0))
        _put_max(d, i1, down)
    return d


def gain_envelope(layout: Iterable, total: float, *, sr: int = SR, base_db: float = -3.0, duck_db: float = -12.0,
                  attack: float = 0.12, release: float = 0.35, fade_in: float = 0.6,
                  fade_out: float = 1.6) -> np.ndarray:
    """Огибающая громкости музыки (линейный множитель) на каждый отсчёт, длина round(total·sr).

    В дБ: base_db − |duck_db|·глубина; поверх — линейные затухания fade_in в начале и fade_out в конце
    (как data-fade-in/out у HyperFrames)."""
    n = int(round(total * sr))
    depth = duck_depth(speech_intervals(layout), n, sr, attack, release)
    g = 10 ** ((base_db - abs(duck_db) * depth) / 20)
    fi, fo = min(int(round(fade_in * sr)), n), min(int(round(fade_out * sr)), n)
    if fi > 0:
        g[:fi] *= np.arange(fi) / fi
    if fo > 0:
        g[n - fo:] *= np.arange(fo, 0, -1) / fo - 1 / fo   # до нуля на последнем отсчёте
    return g


def read_pcm(path: Path, sr: int = SR, channels: int = CHANNELS) -> np.ndarray:
    """Звук (любой формат) → float32 [отсчёты, каналы] через ffmpeg, с пересэмплированием в sr."""
    p = subprocess.run([util.ff(), "-v", "error", "-i", str(path), "-vn", "-ac", str(channels), "-ar", str(sr),
                        "-f", "f32le", "-"], capture_output=True)
    if p.returncode != 0:
        raise util.ToolError(f"ffmpeg не прочитал {path}:\n{p.stderr.decode('utf-8', 'replace')[-1500:]}")
    return np.frombuffer(p.stdout, dtype=np.float32).reshape(-1, channels)


def fit_length(x: np.ndarray, n: int, sr: int = SR, xfade: float = LOOP_XFADE) -> np.ndarray:
    """Ровно n отсчётов: длинный трек подрезается, короткий повторяется встык с равномощным перекрёстным затуханием."""
    if len(x) == 0:
        raise util.ToolError("музыка пустая: нечего подкладывать под монтаж")
    if len(x) >= n:
        return x[:n]
    k = max(1, min(int(round(xfade * sr)), len(x) // 4))
    t = (np.arange(k) + 0.5) / k
    fin, fout = np.sin(np.pi / 2 * t)[:, None], np.cos(np.pi / 2 * t)[:, None]
    parts, tail = [x[:-k]], x[-k:]
    total = len(x) - k
    while total < n:
        parts.append(tail * fout + x[:k] * fin)
        parts.append(x[k:-k])
        tail = x[-k:]
        total += len(x) - k
    parts.append(tail)
    return np.concatenate(parts)[:n]


def write_wav(path: Path, x: np.ndarray, sr: int = SR) -> Path:
    """float [-1, 1] → 16-бит PCM wav (детерминированно, без дизеринга)."""
    import scipy.io.wavfile as wavfile  # noqa: WPS433
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.round(np.clip(x, -1.0, 32767 / 32768) * 32768).astype(np.int16)
    wavfile.write(str(path), sr, pcm)
    return path


def duck_music(music_wav: Path, layout: list, total: float, out: Path, *, base_db: float = -3.0,
               duck_db: float = -12.0, attack: float = 0.12, release: float = 0.35, fade_in: float = 0.6,
               fade_out: float = 1.6) -> Path:
    """Музыка монтажа: 48 кГц стерео, длина ровно total, громкость следует речи раскладки layout.

    layout — раскладка timing.place (Placement: start, length) монтажа; total — его длительность, с.
    Под речью — base_db + duck_db (по умолчанию −15 дБ к music.wav), вне речи — base_db (−3 дБ);
    переходы attack/release, затухания fade_in/fade_out вшиты. В index.html — data-volume="1" без data-fade-*.
    """
    if total <= 0:
        raise ValueError(f"длительность монтажа {total} с: нечего озвучивать")
    n = int(round(total * SR))
    x = fit_length(read_pcm(Path(music_wav)), n)
    g = gain_envelope(layout, total, base_db=base_db, duck_db=duck_db, attack=attack, release=release,
                      fade_in=fade_in, fade_out=fade_out)
    y = x.astype(np.float64) * g[:, None]
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 1.0:
        util.say(f"музыка монтажа: пик {20 * np.log10(peak):+.1f} dBFS — срезан; уменьшите music.duck_base_db", err=True)
    return write_wav(Path(out), y)


__all__ = ["duck_music", "speech_intervals", "duck_depth", "gain_envelope", "fit_length", "read_pcm", "write_wav",
           "SR"]
