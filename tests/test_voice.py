"""Тесты озвучки и сверки: тяжёлые модели (RUAccent, F5, Piper, Whisper) подменены заглушками."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
import scipy.io.wavfile as wavfile

from reels import asr, project, util, voice

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "example" / "voice"
SCRIPT = ["У каждого горожанина есть невидимый поводок.", "Санкт-Паули — это район."]
integration = pytest.mark.skipif(os.environ.get("REELS_INTEGRATION") != "1", reason="REELS_INTEGRATION=1")


def make(tmp_path: Path, **voice_cfg) -> project.Project:
    raw = {"blocks": [{"text": t} for t in SCRIPT], "edits": {"linear": "12"}, "voice": voice_cfg,
           "asr": {"model": "tiny", "device": "cpu"}}
    return project.from_dict(raw, tmp_path)


def tone(path: Path, seconds: float = 1.0, sr: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * sr)) / sr
    wavfile.write(path, sr, (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16))
    return path


# --- asr ----------------------------------------------------------------------
def test_normalize_and_ratio():
    assert asr.normalize("Г+ород — Ёлка, 2024!") == ["город", "елка"]
    assert asr.ratio("У к+аждого гор+ожанина", "у каждого горожанина") == 1.0
    r = asr.ratio("раз два три четыре", "раз два три")
    assert 0.8 < r < 1.0 and r == round(r, 3)


def test_words_format(monkeypatch):
    class W:
        def __init__(self, w, s, e):
            self.word, self.start, self.end = w, s, e

    class Seg:
        words = [W(" У", 0.0, 0.52049), W(" каждого", 0.52, 0.8812), W(" ", 0.9, 0.9)]
        text = " У каждого"

    class Model:
        def transcribe(self, path, language, word_timestamps=False):
            return iter([Seg()]), None

    monkeypatch.setattr(asr, "load", lambda cfg: Model())
    assert asr.words("x.wav", {}) == [{"w": "У", "s": 0.0, "e": 0.52}, {"w": "каждого", "s": 0.52, "e": 0.881}]
    assert asr.transcribe("x.wav", {}) == "У каждого"


# --- ударения -----------------------------------------------------------------
def test_apply_accents():
    acc = {"паули": "Па+ули", "город": "г+ород"}
    assert voice.apply_accents("Санкт-П+аули и городок, Город г+ород.", acc) == \
        "Санкт-Па+ули и городок, Г+ород г+ород."


def test_accent_f5_uses_ruaccent_and_overrides(tmp_path, monkeypatch):
    class Fake:
        def process_all(self, text):
            return text.replace("каждого", "к+аждого").replace("Паули", "П+аули")

    monkeypatch.setattr(voice, "_ruaccent", lambda: Fake())
    p = make(tmp_path, accents={"Паули": "Па+ули"})
    data = util.read_json(voice.accent(p))
    assert "к+аждого" in data["master"] and "Санкт-Па+ули" in data["master"]
    assert data["ref"].startswith("Утром")


def test_accent_piper_plain(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "_ruaccent", lambda: pytest.fail("RUAccent не нужен для piper"))
    p = make(tmp_path, engine="piper")
    data = util.read_json(voice.accent(p))
    assert data["master"] == p.script and "+" not in data["ref"]


# --- эталоны ------------------------------------------------------------------
def test_ensure_refs_generates_with_piper(tmp_path, monkeypatch):
    (tmp_path / "models/piper").mkdir(parents=True)
    (tmp_path / "models/piper/ru_RU-dmitri-medium.onnx").write_bytes(b"x")
    tone(tmp_path / "assets/ref-denis.wav")
    calls = []
    monkeypatch.setattr(voice, "_piper_tts", lambda text, model, out: calls.append(model) or tone(out, 2.0, 22050))
    p = make(tmp_path)
    refs = voice.ensure_refs(p)
    assert refs["denis"] == tmp_path / "assets/ref-denis.wav"
    assert [m.name for m in calls] == ["ru_RU-dmitri-medium.onnx"]
    out = refs["dmitri"]
    info = util.run([util.ff("ffprobe"), "-v", "error", "-show_entries", "stream=sample_rate,channels",
                     "-of", "default=nw=1", out]).stdout
    assert "sample_rate=24000" in info and "channels=1" in info
    assert util.wav_duration(out) == pytest.approx(2.45, abs=0.02)


def test_piper_model_missing(tmp_path):
    with pytest.raises(util.ToolError, match="rhasspy/piper-voices"):
        voice.piper_model(make(tmp_path), "nobody")


# --- дубли --------------------------------------------------------------------
def fake_f5(monkeypatch, heard_by_take: dict):
    class TTS:
        def infer(self, ref, ref_text, text, file_wave, speed, nfe_step, seed, show_info):
            assert ref_text == "эталон"  # тексты — из accents.json
            tone(Path(file_wave), 1.0)

    monkeypatch.setattr(voice, "_f5_model", lambda: TTS())
    monkeypatch.setattr(voice, "ensure_refs", lambda p: {v: Path("ref.wav") for v in p.cfg["voice"]["voices"]})
    monkeypatch.setattr(asr, "transcribe", lambda path, cfg: heard_by_take[Path(path).stem])


def test_synthesize_f5_picks_best(tmp_path, monkeypatch):
    p = make(tmp_path, voices=["denis", "dmitri"], seeds=[7, 11])
    util.write_json(p.voice_dir / "accents.json", {"master": p.script, "ref": "эталон"})
    good, bad = p.script, p.script.replace("невидимый ", "")
    fake_f5(monkeypatch, {"f5-denis-7": bad, "f5-denis-11": bad, "f5-dmitri-7": good, "f5-dmitri-11": good})
    choice = voice.synthesize(p)
    assert choice == {"file": "takes/f5-dmitri-7.wav", "voice": "dmitri", "seed": 7, "ratio": 1.0, "ok": True}
    takes = util.read_json(p.voice_dir / "takes.json")
    assert [t["file"] for t in takes] == ["takes/f5-dmitri-7.wav", "takes/f5-dmitri-11.wav",
                                          "takes/f5-denis-7.wav", "takes/f5-denis-11.wav"]
    assert set(takes[0]) == {"file", "voice", "seed", "ratio", "heard", "duration"}
    assert takes[0]["duration"] == pytest.approx(1.0, abs=0.01)
    assert p.master_wav.read_bytes() == (p.voice_dir / "takes/f5-dmitri-7.wav").read_bytes()
    assert util.read_json(p.voice_dir / "choice.json") == choice


def test_synthesize_below_min_ratio(tmp_path, monkeypatch):
    p = make(tmp_path, voices=["denis"], seeds=[7, 11])
    util.write_json(p.voice_dir / "accents.json", {"master": p.script, "ref": "эталон"})
    fake_f5(monkeypatch, {"f5-denis-7": "совсем не то", "f5-denis-11": p.script.replace("Санкт-Паули", "")})
    choice = voice.synthesize(p)
    assert choice["seed"] == 11 and choice["ok"] is False and choice["ratio"] < 0.98
    assert p.master_wav.exists()


def test_synthesize_piper(tmp_path, monkeypatch):
    for v in ("denis", "dmitri"):
        (tmp_path / "models/piper").mkdir(parents=True, exist_ok=True)
        (tmp_path / f"models/piper/ru_RU-{v}-medium.onnx").write_bytes(b"x")
    texts = []
    monkeypatch.setattr(voice, "_piper_tts", lambda text, model, out: texts.append(text) or tone(out, 1.0, 22050))
    monkeypatch.setattr(asr, "transcribe", lambda path, cfg: "" if "denis" in str(path) else SCRIPT[0] + SCRIPT[1])
    p = make(tmp_path, engine="piper")
    choice = voice.synthesize(p)
    assert choice["file"] == "takes/piper-dmitri.wav" and choice["seed"] is None and choice["ok"]
    assert texts == [p.script, p.script]


def test_synthesize_file_without_asr(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "available", lambda: False)
    p = make(tmp_path, engine="file", file=str(EXAMPLE / "voice.wav"))
    choice = voice.synthesize(p)
    assert choice == {"file": "takes/file.wav", "voice": None, "seed": None, "ratio": None, "ok": True}
    info = util.run([util.ff("ffprobe"), "-v", "error", "-show_entries", "stream=sample_rate",
                     "-of", "default=nw=1", p.master_wav]).stdout
    assert "sample_rate=48000" in info
    assert util.wav_duration(p.master_wav) == pytest.approx(22.4, abs=0.05)


def test_synthesize_file_with_asr(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "available", lambda: True)
    monkeypatch.setattr(asr, "transcribe", lambda path, cfg: "у каждого горожанина")
    p = make(tmp_path, engine="file", file=str(EXAMPLE / "voice.wav"))
    choice = voice.synthesize(p)
    assert choice["ratio"] < 0.98 and choice["ok"] is False


# --- таймкоды -----------------------------------------------------------------
def test_timestamps_file_words(tmp_path):
    p = make(tmp_path, engine="file", file=str(EXAMPLE / "voice.wav"), words=str(EXAMPLE / "words.json"))
    out = voice.timestamps(p)
    assert out == p.words_json and util.read_json(out) == util.read_json(EXAMPLE / "words.json")


def test_timestamps_whisper(tmp_path, monkeypatch):
    p = make(tmp_path, engine="piper")
    tone(p.master_wav)
    ws = [{"w": "У", "s": 0.0, "e": 0.5}]
    monkeypatch.setattr(asr, "words", lambda path, cfg: ws if Path(path) == p.master_wav else None)
    assert util.read_json(voice.timestamps(p)) == ws


# --- настоящие модели (REELS_INTEGRATION=1) -------------------------------------
@integration
def test_real_whisper_words():
    pytest.importorskip("faster_whisper")
    cfg = {"model": os.environ.get("REELS_ASR_MODEL", "tiny"), "device": "cpu"}
    ws = asr.words(EXAMPLE / "voice.wav", cfg)
    assert ws and all(set(w) == {"w", "s", "e"} for w in ws)
    script = " ".join(w["w"] for w in util.read_json(EXAMPLE / "words.json"))
    assert asr.ratio(script, " ".join(w["w"] for w in ws)) > 0.6


@integration
def test_real_piper_ref(tmp_path):
    pytest.importorskip("piper")
    models = os.environ.get("PIPER_MODELS")
    if not models or not (Path(models) / "ru_RU-dmitri-medium.onnx").exists():
        pytest.skip("нужен PIPER_MODELS с ru_RU-dmitri-medium.onnx")
    p = make(tmp_path, voices=["dmitri"])
    ref = voice.ensure_refs(p)["dmitri"]
    assert 5 < util.wav_duration(ref) < 15
