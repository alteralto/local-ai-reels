"""Тесты движка озвучки qwen3 (Qwen3-TTS): модель, torch и Whisper подменены заглушками.

Заглушка повторяет интерфейс qwen_tts.Qwen3TTSModel (модель Base):
    create_voice_clone_prompt(ref_audio, ref_text=None, x_vector_only_mode=False) -> list[VoiceClonePromptItem]
    generate_voice_clone(text, language=None, ref_audio=None, ref_text=None, x_vector_only_mode=False,
                         voice_clone_prompt=None, non_streaming_mode=False, **kwargs) -> (list[np.ndarray], sr)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest
import scipy.io.wavfile as wavfile

from reels import asr, project, util, voice

SCRIPT = ["У каждого горожанина есть невидимый поводок.", "Санкт-Паули — это район."]
SR = 24000


def make(tmp_path: Path, **voice_cfg) -> project.Project:
    raw = {"blocks": [{"text": t} for t in SCRIPT], "edits": {"linear": "12"},
           "voice": {"engine": "qwen3", **voice_cfg}, "asr": {"model": "tiny", "device": "cpu"}}
    return project.from_dict(raw, tmp_path)


def tone(path: Path, seconds: float = 1.0, sr: int = SR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * sr)) / sr
    wavfile.write(path, sr, (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16))
    return path


class FakeQwen:
    """Qwen3TTSModel: пишет вызовы в log, возвращает 1 с синуса (float32, 24 кГц)."""

    def __init__(self, log: list, seconds: float = 1.0):
        self.log, self.seconds = log, seconds

    def create_voice_clone_prompt(self, ref_audio, ref_text=None, x_vector_only_mode=False):
        self.log.append(("prompt", ref_audio, ref_text, x_vector_only_mode))
        return [{"ref_audio": ref_audio}]

    def generate_voice_clone(self, text, language=None, ref_audio=None, ref_text=None, x_vector_only_mode=False,
                             voice_clone_prompt=None, non_streaming_mode=False, **kwargs):
        assert ref_audio is None and ref_text is None  # эталон — через готовый voice_clone_prompt
        self.log.append(("gen", text, language, voice_clone_prompt[0]["ref_audio"]))
        t = np.arange(int(self.seconds * SR)) / SR
        return [(0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)], SR


@pytest.fixture
def fake_torch(monkeypatch):
    """Подмена torch: manual_seed пишется в общий log."""
    log: list = []
    mod = types.ModuleType("torch")
    mod.manual_seed = lambda s: log.append(("seed", s))
    mod.bfloat16, mod.float32 = "bfloat16", "float32"
    monkeypatch.setitem(sys.modules, "torch", mod)
    return log


def qwen_setup(tmp_path, monkeypatch, log, heard: dict, accents: dict | None = None, **voice_cfg):
    p = make(tmp_path, **voice_cfg)
    util.write_json(p.voice_dir / "accents.json", accents or {
        "master": "У к+аждого гор+ожанина есть нев+идимый пов+одок. С+анкт-П+аули — +это рай+он.",
        "ref": "Утр+ом на пл+ощади п+усто."})
    monkeypatch.setattr(voice, "ensure_refs",
                        lambda pr: {v: tmp_path / f"assets/ref-{v}.wav" for v in pr.cfg["voice"]["voices"]})
    monkeypatch.setattr(voice, "_qwen_model", lambda pr: FakeQwen(log))
    monkeypatch.setattr(asr, "transcribe", lambda path, cfg: heard[Path(path).stem])
    return p


# --- конфиг и текст -------------------------------------------------------------
def test_config_accepts_qwen3(tmp_path):
    p = make(tmp_path)
    v = p.cfg["voice"]
    assert "qwen3" in project.VOICE_ENGINES and v["engine"] == "qwen3"
    assert v["qwen_model"] == "Qwen/Qwen3-TTS-12Hz-1.7B-Base" and v["qwen_language"] == "Russian"
    assert v["qwen_keep_accents"] is False
    with pytest.raises(project.ConfigError, match="qwen3"):
        make(tmp_path, engine="nope")


def test_strip_stress():
    assert voice.strip_stress("У к+аждого гор+ожанина, +Это С+анкт-П+аули!") == "У каждого горожанина, Это Санкт-Паули!"
    assert voice.strip_stress("2+2 и C++ и + отдельно") == "2+2 и C++ и + отдельно"


def test_accent_qwen3_plain_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "_ruaccent", lambda: pytest.fail("RUAccent не нужен: Qwen3 не читает «+»"))
    p = make(tmp_path)
    data = util.read_json(voice.accent(p))
    assert data["master"] == p.script and "+" not in data["ref"]


def test_accent_qwen3_keep_accents_uses_ruaccent(tmp_path, monkeypatch):
    class Fake:
        def process_all(self, text):
            return text.replace("каждого", "к+аждого")

    monkeypatch.setattr(voice, "_ruaccent", lambda: Fake())
    p = make(tmp_path, qwen_keep_accents=True, accents={"Паули": "Па+ули"})
    data = util.read_json(voice.accent(p))
    assert "к+аждого" in data["master"] and "Санкт-Па+ули" in data["master"]


# --- дубли --------------------------------------------------------------------------
def test_synthesize_qwen3_picks_best(tmp_path, monkeypatch, fake_torch):
    log = fake_torch  # сиды и вызовы модели — в одном журнале, чтобы проверить порядок
    good, bad = " ".join(SCRIPT), " ".join(SCRIPT).replace("невидимый ", "")
    p = qwen_setup(tmp_path, monkeypatch, log, voices=["denis", "dmitri"], seeds=[7, 11],
                   heard={"qwen3-denis-7": bad, "qwen3-denis-11": good, "qwen3-dmitri-7": good,
                          "qwen3-dmitri-11": bad})
    choice = voice.synthesize(p)

    assert choice == {"file": "takes/qwen3-denis-11.wav", "voice": "denis", "seed": 11, "ratio": 1.0, "ok": True}
    assert util.read_json(p.voice_dir / "choice.json") == choice
    takes = util.read_json(p.voice_dir / "takes.json")
    assert [t["file"] for t in takes] == ["takes/qwen3-denis-11.wav", "takes/qwen3-dmitri-7.wav",
                                          "takes/qwen3-denis-7.wav", "takes/qwen3-dmitri-11.wav"]
    assert [(t["voice"], t["seed"]) for t in takes] == [("denis", 11), ("dmitri", 7), ("denis", 7), ("dmitri", 11)]
    assert set(takes[0]) == {"file", "voice", "seed", "ratio", "heard", "duration"}
    assert takes[0]["heard"] == good and takes[-1]["ratio"] < 0.98
    assert takes[0]["duration"] == pytest.approx(1.0, abs=0.01)
    assert p.master_wav.read_bytes() == (p.voice_dir / "takes/qwen3-denis-11.wav").read_bytes()
    sr, data = wavfile.read(p.master_wav)
    assert sr == SR and data.dtype == np.int16 and len(data) == SR and 0.25 < data.max() / 32767 < 0.35

    # эталон на голос — один раз; текст эталона и сценарий без «+»; сид — перед каждым дублем
    ref = {v: str(tmp_path / f"assets/ref-{v}.wav") for v in ("denis", "dmitri")}
    text = "У каждого горожанина есть невидимый поводок. Санкт-Паули — это район."
    assert log == [
        ("prompt", ref["denis"], "Утром на площади пусто.", False),
        ("seed", 7), ("gen", text, "Russian", ref["denis"]),
        ("seed", 11), ("gen", text, "Russian", ref["denis"]),
        ("prompt", ref["dmitri"], "Утром на площади пусто.", False),
        ("seed", 7), ("gen", text, "Russian", ref["dmitri"]),
        ("seed", 11), ("gen", text, "Russian", ref["dmitri"]),
    ]


def test_synthesize_qwen3_keep_accents_and_language(tmp_path, monkeypatch, fake_torch):
    log = fake_torch
    acc = {"master": "У к+аждого гор+ожанина.", "ref": "Утр+ом."}
    p = qwen_setup(tmp_path, monkeypatch, log, heard={"qwen3-denis-3": " ".join(SCRIPT)}, accents=acc,
                   voices=["denis"], seeds=[3], qwen_keep_accents=True, qwen_language="Auto")
    voice.synthesize(p)
    assert ("prompt", str(tmp_path / "assets/ref-denis.wav"), "Утр+ом.", False) in log
    assert ("gen", "У к+аждого гор+ожанина.", "Auto", str(tmp_path / "assets/ref-denis.wav")) in log


def test_synthesize_qwen3_below_min_ratio(tmp_path, monkeypatch, fake_torch):
    almost = " ".join(SCRIPT).replace("Санкт-Паули", "")
    p = qwen_setup(tmp_path, monkeypatch, fake_torch, voices=["denis"], seeds=[7, 11],
                   heard={"qwen3-denis-7": "совсем не то", "qwen3-denis-11": almost})
    choice = voice.synthesize(p)
    assert choice["seed"] == 11 and choice["ok"] is False and choice["ratio"] < 0.98
    assert p.master_wav.exists()


def test_synthesize_qwen3_generates_refs_with_piper(tmp_path, monkeypatch, fake_torch):
    """Эталона нет — ensure_refs озвучивает ref_text голосом Piper (как для F5), и Qwen3 клонирует его."""
    (tmp_path / "models/piper").mkdir(parents=True)
    (tmp_path / "models/piper/ru_RU-dmitri-medium.onnx").write_bytes(b"x")
    piper_texts = []
    monkeypatch.setattr(voice, "_piper_tts",
                        lambda text, model, out: piper_texts.append(text) or tone(out, 2.0, 22050))
    monkeypatch.setattr(voice, "_qwen_model", lambda pr: FakeQwen(fake_torch))
    monkeypatch.setattr(asr, "transcribe", lambda path, cfg: " ".join(SCRIPT))
    p = make(tmp_path, voices=["dmitri"], seeds=[5])
    choice = voice.synthesize(p)
    ref = tmp_path / "assets/ref-dmitri.wav"
    assert ref.exists() and choice["ok"] and choice["file"] == "takes/qwen3-dmitri-5.wav"
    assert piper_texts == [" ".join(p.cfg["voice"]["ref_text"].split())]
    prompt = [e for e in fake_torch if e[0] == "prompt"]
    assert prompt == [("prompt", str(ref), " ".join(p.cfg["voice"]["ref_text"].split()), False)]
    info = util.run([util.ff("ffprobe"), "-v", "error", "-show_entries", "stream=sample_rate,channels",
                     "-of", "default=nw=1", ref]).stdout
    assert "sample_rate=24000" in info and "channels=1" in info


def test_write_wav_empty_audio(tmp_path):
    with pytest.raises(util.ToolError, match="пустой звук"):
        voice._write_wav(tmp_path / "x.wav", np.zeros(0, dtype=np.float32), SR)


# --- сиды -------------------------------------------------------------------------
def test_seed_sets_torch_numpy_random(fake_torch):
    import random
    voice._seed(42)
    a, r = np.random.rand(), random.random()
    voice._seed(42)
    assert (np.random.rand(), random.random()) == (a, r)
    assert fake_torch == [("seed", 42), ("seed", 42)]
    voice._seed(None)
    assert len(fake_torch) == 2


def test_seed_without_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # import torch → ImportError
    voice._seed(5)
    a = np.random.rand()
    voice._seed(5)
    assert np.random.rand() == a


# --- загрузка модели ----------------------------------------------------------------
def fake_qwen_pkg(monkeypatch) -> list:
    calls = []

    class Qwen3TTSModel:
        @classmethod
        def from_pretrained(cls, src, **kw):
            calls.append((src, kw))
            return "model"

    mod = types.ModuleType("qwen_tts")
    mod.Qwen3TTSModel = Qwen3TTSModel
    monkeypatch.setitem(sys.modules, "qwen_tts", mod)
    return calls


def test_qwen_model_missing_package(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "qwen_tts", None)
    with pytest.raises(util.ToolError, match="pip install -U qwen-tts"):
        voice._qwen_model(make(tmp_path))


def test_qwen_model_cuda_hf_id(tmp_path, monkeypatch, fake_torch):
    calls = fake_qwen_pkg(monkeypatch)
    monkeypatch.setitem(sys.modules, "flash_attn", None)
    monkeypatch.setattr(util, "device", lambda pref="auto": "cuda")
    monkeypatch.setenv("TTS_MODELS", str(tmp_path / "no-models"))
    assert voice._qwen_model(make(tmp_path)) == "model"
    assert calls == [("Qwen/Qwen3-TTS-12Hz-1.7B-Base", {"device_map": "cuda:0", "dtype": "bfloat16"})]


def test_qwen_model_cpu_local_dir_flash_attn(tmp_path, monkeypatch, fake_torch):
    calls = fake_qwen_pkg(monkeypatch)
    local = tmp_path / "models/Qwen3-TTS-12Hz-0.6B-Base"
    local.mkdir(parents=True)
    monkeypatch.setenv("TTS_MODELS", str(tmp_path / "models"))
    monkeypatch.setattr(util, "device", lambda pref="auto": "cpu")
    voice._qwen_model(make(tmp_path, qwen_model="Qwen/Qwen3-TTS-12Hz-0.6B-Base"))
    assert calls[-1] == (str(local), {"device_map": "cpu", "dtype": "float32"})

    monkeypatch.setitem(sys.modules, "flash_attn", types.ModuleType("flash_attn"))
    monkeypatch.setattr(util, "device", lambda pref="auto": "cuda")
    voice._qwen_model(make(tmp_path, qwen_model="weights/qwen"))  # нет такой папки — id как есть
    assert calls[-1] == ("weights/qwen", {"device_map": "cuda:0", "dtype": "bfloat16",
                                          "attn_implementation": "flash_attention_2"})
