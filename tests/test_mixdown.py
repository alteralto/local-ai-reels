"""Тесты приглушения музыки под голос (reels.mixdown): огибающая, длительность, плавность, повтор трека."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import scipy.io.wavfile as wavfile

from reels import mixdown as mx
from reels import timing as tm

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "example" / "music" / "music.wav"
CUTS = [0.0, 4.2, 11.45, 17.6, 22.4]
SR = mx.SR
ATTACK, RELEASE, FADE_IN, FADE_OUT = 0.12, 0.35, 0.6, 1.6


def read(path: Path) -> np.ndarray:
    sr, x = wavfile.read(path)
    assert sr == SR and x.dtype == np.int16 and x.ndim == 2 and x.shape[1] == 2
    return x.astype(np.float64) / 32768


def rms_db(x: np.ndarray, a: float, b: float) -> float:
    seg = x[int(round(a * SR)):int(round(b * SR))]
    return 20 * np.log10(np.sqrt(np.mean(seg ** 2)))


def sine(path: Path, seconds: float, gain: float = 0.5, sr: int = 44100) -> Path:
    """Стерео синус 220 Гц постоянной громкости — у него RMS окна не зависит от места в треке."""
    t = np.arange(int(seconds * sr)) / sr
    x = gain * np.sin(2 * np.pi * 220 * t)
    wavfile.write(path, sr, np.stack([x, x], axis=1).astype(np.float32))
    return path


def plateaus(iv: list[tuple[float, float]], total: float) -> tuple[list, list]:
    """Окна, где огибающая уже на месте: речь (без вступительного затухания) и паузы/карточка (без переходов)."""
    speech = [(max(s, FADE_IN), e) for s, e in iv if e > FADE_IN]
    rest = [(e + RELEASE, s1 - ATTACK) for (_, e), (s1, _) in zip(iv, iv[1:])]
    rest.append((iv[-1][1] + RELEASE, total - FADE_OUT))
    return speech, [(a, b) for a, b in rest if b - a > 0.1]


def test_speech_intervals():
    lay, total = tm.place([1, 2, 3, 4], CUTS)
    assert total == pytest.approx(24.26)
    assert mx.speech_intervals(lay) == [(0.0, 4.2), (4.32, 11.57), (11.69, 17.84), (17.96, 22.76)]
    assert mx.speech_intervals([(5, 6), (0, 1), (0.5, 2), (3, 3)]) == [(0.0, 2.0), (5.0, 6.0)]
    assert mx.speech_intervals(lay, merge_gap=0.12) == [(0.0, 22.76)]


def test_example_ducked_track(tmp_path):
    """Музыка примера, раскладка place([1,2,3,4]): длина ровно total, под речью −15 дБ к исходнику, плавно."""
    lay, total = tm.place([1, 2, 3, 4], CUTS)
    out = mx.duck_music(EXAMPLE, lay, total, tmp_path / "music.wav")
    y = read(out)
    assert len(y) == round(total * SR)                       # ровно длительность монтажа
    src = mx.fit_length(mx.read_pcm(EXAMPLE), len(y)).astype(np.float64)
    g = mx.gain_envelope(lay, total)
    assert np.abs(y - src * g[:, None]).max() <= 1 / 32768   # файл = музыка × огибающая (с точностью до 1 LSB)
    for s, e in plateaus(mx.speech_intervals(lay), total)[0]:
        assert rms_db(y, s, e) - rms_db(src, s, e) == pytest.approx(-15.0, abs=0.1)
    # короткие паузы между блоками (0.12 с) — лишь небольшой подъём, без «качания»
    gd = 20 * np.log10(g[int(FADE_IN * SR):-int(FADE_OUT * SR)])
    assert gd.max() < -12.0
    # нет скачков: шаг огибающей от отсчёта к отсчёту
    assert np.abs(np.diff(gd)).max() < 0.005                 # дБ на отсчёт
    assert np.abs(np.diff(g)).max() < 1e-3
    assert y[0].max() == 0 and np.abs(y[-1]).max() == 0       # затухания вшиты


def test_duck_depth_matches_duck_db(tmp_path):
    """Постоянный синус вместо музыки: RMS под речью на duck_db ниже, чем в паузах и на финальной карточке."""
    music = sine(tmp_path / "sine.wav", 40.0)
    lay, total = tm.place([1, 2, 3, 4], CUTS, gap=1.0, tail=3.0)
    for duck_db in (-12.0, -8.0):
        y = read(mx.duck_music(music, lay, total, tmp_path / "m.wav", duck_db=duck_db))
        assert len(y) == round(total * SR)
        speech, rest = plateaus(mx.speech_intervals(lay), total)
        assert len(speech) == 4 and len(rest) == 4                  # 3 паузы + карточка
        ref = rms_db(y, *rest[0])
        src = 20 * np.log10(0.5 / np.sqrt(2))
        assert ref - src == pytest.approx(-3.0, abs=0.05)            # base_db
        for w in rest:
            assert rms_db(y, *w) == pytest.approx(ref, abs=0.05)
        for w in speech:
            assert rms_db(y, *w) - ref == pytest.approx(duck_db, abs=0.05)


def test_loops_short_music(tmp_path):
    """Трек короче монтажа повторяется без провалов, длина — ровно total."""
    noise = np.random.default_rng(0).normal(0, 0.1, (3 * 48000, 2))   # некоррелированный стык, как у музыки
    music = tmp_path / "short.wav"
    wavfile.write(music, 48000, noise.astype(np.float32))
    lay, total = tm.place([1, 2], [0.0, 2.0, 4.0], gap=0.5, tail=4.0)
    y = read(mx.duck_music(music, lay, total, tmp_path / "m.wav", duck_db=0.0, fade_in=0, fade_out=0))
    assert len(y) == round(total * SR) and total > 8
    win = [rms_db(y, a, a + 0.25) for a in np.arange(0, total - 0.25, 0.25)]
    assert max(win) - min(win) < 1.0                                 # стыки с равномощным перекрёстным затуханием


def test_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        mx.duck_music(EXAMPLE, [], 0, tmp_path / "m.wav")
