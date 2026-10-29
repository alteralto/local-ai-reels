"""Тесты музыки: провалы громкости, LUFS, движки file/none/musicgen (MusicGen подменён заглушкой)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import scipy.io.wavfile as wavfile

from reels import music, project, util

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "example" / "music" / "music.wav"


def make(tmp_path: Path, **music_cfg) -> project.Project:
    return project.from_dict({"blocks": ["Раз два три."], "edits": {"linear": "1"}, "music": music_cfg}, tmp_path)


def sine(path: Path, seconds: float, hole: tuple[float, float] | None = None, sr: int = 32000,
         gain: float = 0.3) -> Path:
    """Синус 330 Гц; hole — тихий участок (−30 дБ)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * sr)) / sr
    x = gain * np.sin(2 * np.pi * 330 * t)
    if hole:
        x[int(hole[0] * sr):int(hole[1] * sr)] *= 10 ** (-30 / 20)
    wavfile.write(path, sr, x.astype(np.float32))
    return path


def test_dips_finds_quiet_hole(tmp_path):
    f = sine(tmp_path / "a.wav", 20.0, hole=(8.0, 10.0))
    d = music.dips(f)
    assert len(d) == 1
    assert d[0]["start"] == 8.0 and d[0]["end"] == 10.0 and d[0]["drop"] == pytest.approx(30, abs=1)


def test_dips_clean_and_partial_last_window(tmp_path):
    assert music.dips(sine(tmp_path / "b.wav", 20.0)) == []
    f = sine(tmp_path / "c.wav", 21.0, hole=(20.0, 21.0))  # тихий хвост в неполном окне — не считается
    assert music.dips(f) == []


def test_loudness_and_normalize(tmp_path):
    out = music.normalize(EXAMPLE, tmp_path / "n.wav")
    assert music.loudness(out) == pytest.approx(-18.0, abs=1.0)
    info = util.run([util.ff("ffprobe"), "-v", "error", "-show_entries", "stream=sample_rate,channels",
                     "-of", "default=nw=1", out]).stdout
    assert "sample_rate=48000" in info and "channels=2" in info


def test_engine_none(tmp_path):
    assert music.make_music(make(tmp_path, engine="none"), 20) is None


def test_engine_file(tmp_path):
    p = make(tmp_path, engine="file", file=str(EXAMPLE))
    out = music.make_music(p, 20.0)
    assert out == p.music_wav and util.wav_duration(out) == pytest.approx(27.94, abs=0.05)
    assert music.loudness(out) == pytest.approx(-18.0, abs=1.0)


def test_engine_file_extends_short_track(tmp_path):
    src = sine(tmp_path / "short.wav", 6.0)
    p = make(tmp_path, engine="file", file=str(src))
    out = music.make_music(p, 15.0)
    assert util.wav_duration(out) == pytest.approx(15.0, abs=0.05)
    assert music.dips(out) == []  # стыки без провалов
    assert music.loudness(out) == pytest.approx(-18.0, abs=1.5)


def test_engine_musicgen_picks_variant_without_dips(tmp_path, monkeypatch):
    calls = []

    def fake(prompt, seed, seconds, out, model=music.MODEL):
        calls.append((prompt, seed, seconds, model))
        return sine(out, 12.0, hole=(4.0, 6.0) if seed == 5 else None)

    monkeypatch.setattr(music, "_generate", fake)
    p = make(tmp_path, prompts=["a", "b"], seed=5)
    out = music.make_music(p, 20.0)
    assert [c[:2] for c in calls] == [("a", 5), ("b", 6)] and calls[0][3] == "facebook/musicgen-small"
    choice = util.read_json(p.music_dir / "choice.json")
    assert choice["file"] == "variant-1.wav" and choice["index"] == 1
    assert len(choice["variants"][0]["dips"]) == 1 and choice["variants"][1]["dips"] == []
    assert (p.music_dir / "variant-0.wav").exists() and not (p.music_dir / "variant-0.raw.wav").exists()
    assert util.wav_duration(out) == pytest.approx(20.0, abs=0.05)


def test_engine_musicgen_tie_takes_shallower(tmp_path, monkeypatch):
    def fake(prompt, seed, seconds, out, model=music.MODEL):
        x = sine(out, 30.0, hole=(10.0, 12.0))
        if seed == 5:  # первый вариант — провал глубже
            sr, a = wavfile.read(x)
            a[10 * sr:12 * sr] *= 0.1
            wavfile.write(x, sr, a)
        return x

    monkeypatch.setattr(music, "_generate", fake)
    p = make(tmp_path, prompts=["a", "b"], seed=5)
    music.make_music(p, 25.0)
    assert util.read_json(p.music_dir / "choice.json")["index"] == 1
