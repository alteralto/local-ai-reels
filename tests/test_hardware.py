"""Профили железа: определение, выбор настроек, переопределения, запасные движки, doctor. Видеокарта не нужна."""
import json
import wave
from pathlib import Path

import pytest

from conftest import BLOCKS
from reels import asr, hardware as H, music, util, voice
from reels.project import ConfigError, from_dict

PKGS = {k: True for k in ("faster-whisper", "f5-tts", "qwen-tts", "ruaccent", "piper-tts", "transformers", "scipy",
                          "torch")}


def hw_cuda(vram=24.0, name="NVIDIA GeForce RTX 4090", n=1, **kw):
    gpus = [H.GPU(i, name, vram, "560.94") for i in range(n)]
    base = dict(os="linux", arch="x86_64", cpu="Ryzen 9", cores_physical=16, cores_logical=32, ram_gb=64.0, gpus=gpus,
                nvidia_smi="/usr/bin/nvidia-smi", torch="2.5.1+cu124", torch_cuda=True, torch_cuda_version="12.4",
                torch_mps=False, ct2_cuda=n, ffmpeg="/usr/bin/ffmpeg", ffmpeg_version="7.1",
                hw_encoders=["h264_nvenc"], node="v22.11.0", npx="/usr/bin/npx", packages=dict(PKGS), deep=True)
    return H.Hardware(**{**base, **kw})


def hw_mps(**kw):
    base = dict(os="darwin", arch="arm64", cpu="Apple M3 Pro", cores_physical=12, cores_logical=12, ram_gb=36.0,
                torch="2.5.1", torch_cuda=False, torch_mps=True, ffmpeg="/opt/homebrew/bin/ffmpeg",
                hw_encoders=["h264_videotoolbox"], node="v22.11.0", npx="/opt/homebrew/bin/npx",
                packages=dict(PKGS), deep=True)
    return H.Hardware(**{**base, **kw})


def hw_cpu(cores=4, ram=8.0, **kw):
    base = dict(os="windows", arch="x86_64", cpu="Core i5", cores_physical=cores, cores_logical=cores * 2,
                ram_gb=ram, torch=None, ffmpeg="C:/ffmpeg/bin/ffmpeg.exe", hw_encoders=["h264_nvenc", "h264_qsv"],
                node="v22.11.0", npx="npx.cmd", packages=dict(PKGS, torch=False), deep=True)
    return H.Hardware(**{**base, **kw})


@pytest.fixture(autouse=True)
def _clean():
    H.reset()
    yield
    H.reset()


# --- разбор вывода ------------------------------------------------------------------------------------------------
def test_parse_nvidia_smi_and_encoders():
    g = H.parse_nvidia_smi("0, NVIDIA GeForce RTX 3060, 12288, 560.94\n1, Tesla T4, 15360, 535.10\n")
    assert [(x.index, x.name, x.vram_gb, x.driver) for x in g] == [
        (0, "NVIDIA GeForce RTX 3060", 12.0, "560.94"), (1, "Tesla T4", 15.0, "535.10")]
    out = (" V....D libx264              libx264 H.264\n V....D h264_nvenc           NVIDIA NVENC\n"
           " V....D h264_videotoolbox    VideoToolbox\n A....D aac                  AAC\n")
    assert H.parse_encoders(out) == ["h264_nvenc", "h264_videotoolbox"]


def test_detect_runs_and_serializes():
    hw = H.detect()
    d = hw.to_dict()
    assert d["cores_physical"] >= 1 and d["os"] in ("linux", "windows", "darwin")
    json.dumps(d)
    assert H.detect() is hw  # кэш


# --- матрица профилей ---------------------------------------------------------------------------------------------
def test_cuda_24gb_gpu_max():
    a = H.resolve({}, hw_cuda(24.0))
    assert a.profile == "gpu-max" and a.device == "cuda" and a.torch_device == "cuda"
    assert (a.whisper_device, a.whisper_compute, a.whisper_model) == ("cuda", "float16", None)
    assert a.music_variants is None and not a.sequential and a.encoder == "h264_nvenc"
    assert a.render_workers == 6 and a.threads == 16
    assert a.voice_plan("f5") == ("f5", None)
    assert a.music_plan("musicgen", 2) == ("musicgen", 2, None)


def test_cuda_6gb_lowvram():
    a = H.resolve({}, hw_cuda(6.0, "NVIDIA GeForce RTX 2060"))
    assert a.profile == "gpu-lowvram" and a.device == "cuda"
    assert (a.whisper_device, a.whisper_compute) == ("cuda", "int8_float16")
    assert a.sequential and a.music_variants == 1 and a.music_model == "facebook/musicgen-small"
    assert a.music_plan("musicgen", 2)[:2] == ("musicgen", 1)


def test_8gb_card_is_not_lowvram():
    assert H.resolve({}, hw_cuda(7.99, "RTX 4060")).profile == "gpu-max"


def test_mps_apple():
    a = H.resolve({}, hw_mps())
    assert a.profile == "apple" and a.device == "mps" and a.torch_device == "mps"
    assert (a.whisper_device, a.whisper_compute, a.whisper_threads) == ("cpu", "int8", 12)
    assert a.encoder == "h264_videotoolbox"
    assert a.voice_plan("f5") == ("f5", None)


def test_cpu_4_cores_fast():
    a = H.resolve({}, hw_cpu(4, 8.0))
    assert a.profile == "cpu-fast" and a.device == "cpu"
    assert (a.whisper_model, a.whisper_device, a.whisper_compute, a.whisper_threads) == ("small", "cpu", "int8", 4)
    assert a.encoder == "libx264"  # nvenc есть в сборке ffmpeg, но видеокарты нет
    assert a.render_workers == 2
    engine, note = a.voice_plan("f5")
    assert engine == "piper" and "allow_slow" in note
    assert a.voice_plan("qwen3")[0] == "piper" and a.voice_plan("file") == ("file", None)
    assert a.music_plan("musicgen", 2)[:2] == ("none", 0)
    assert a.music_plan("file", 2) == ("file", 0, None)


def test_cpu_32_cores():
    a = H.resolve({}, hw_cpu(32, 128.0))
    assert a.profile == "cpu-fast" and a.threads == 32 and a.whisper_threads == 32 and a.render_workers == 6


def test_cpu_allow_slow_keeps_engines_one_variant():
    a = H.resolve({"allow_slow": True}, hw_cpu())
    assert a.voice_plan("f5")[0] == "f5"
    assert a.music_plan("musicgen", 3)[:2] == ("musicgen", 1)


def test_cpu_quality_same_models_on_cpu():
    a = H.resolve({"profile": "cpu-quality"}, hw_cuda())
    assert a.device == "cpu" and a.whisper_device == "cpu" and a.whisper_model is None
    assert a.allow_slow and a.voice_plan("f5")[0] == "f5" and a.music_plan("musicgen", 2)[:2] == ("musicgen", 2)
    assert a.encoder == "libx264"


def test_nvidia_without_cuda_torch_warns():
    hw = hw_cuda(12.0, "RTX 3060", torch="2.5.1+cpu", torch_cuda=False, ct2_cuda=0)
    a = H.resolve({}, hw)
    assert a.profile == "cpu-fast" and "не видят" in a.reason
    rep = H.doctor_report(hw=hw, env={})
    assert "--force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu126" in rep
    assert "cuDNN" in rep


def test_torch_index_by_driver():
    assert H.torch_index(hw_cuda()) == "cu126"
    assert H.torch_index(hw_cuda(gpus=[H.GPU(0, "RTX 3060", 12.0)])) == "cu124"  # драйвер неизвестен
    assert H.torch_index(hw_cuda(gpus=[H.GPU(0, "RTX 5090", 32.0, "575.1")])) == "cu128"
    assert H.torch_index(hw_cuda(gpus=[H.GPU(0, "RTX 3060", 12.0, "551.2")])) == "cu124"
    assert H.torch_index(hw_cpu()) == "cpu"


def test_multi_gpu_cuda_device():
    hw = hw_cuda(24.0, n=2)
    a = H.resolve({"cuda_device": 1}, hw)
    assert a.cuda_device == 1 and a.torch_device == "cuda:1"
    a = H.resolve({}, hw, env={"REELS_DEVICE": "cuda:1"})
    assert a.torch_device == "cuda:1" and a.sources["device"] == "переменная REELS_DEVICE"
    a = H.resolve({"cuda_device": 5}, hw)
    assert a.cuda_device == 0 and a.warnings


# --- переопределения ----------------------------------------------------------------------------------------------
def test_env_overrides_yaml():
    hw = hw_cuda()
    a = H.resolve({"profile": "gpu-max", "threads": 8}, hw, env={"REELS_PROFILE": "cpu-fast", "REELS_THREADS": "3"})
    assert a.profile == "cpu-fast" and a.threads == 3 and a.device == "cpu"
    assert "REELS_PROFILE" in a.reason
    a = H.resolve({}, hw, env={"REELS_DEVICE": "cpu"})
    assert a.profile == "cpu-fast" and a.whisper_device == "cpu"
    a = H.resolve({}, hw_cpu(), env={"REELS_ALLOW_SLOW": "1"})
    assert a.voice_plan("f5")[0] == "f5"


def test_explicit_settings():
    a = H.resolve({"whisper_model": "medium", "whisper_compute": "int8", "music_variants": 3, "render_workers": 2,
                   "encoder": "libx264", "tts_engine_fallback": "none"}, hw_cpu())
    assert (a.whisper_model, a.whisper_pinned, a.whisper_compute) == ("medium", True, "int8")
    assert a.render_workers == 2 and a.encoder == "libx264"
    assert a.voice_plan("f5")[0] == "f5"  # замена выключена — медленно, но как просили
    a = H.resolve({"allow_slow": True, "music_variants": 3}, hw_cpu())
    assert a.music_plan("musicgen", 2)[1] == 2


@pytest.mark.parametrize("cfg, msg", [
    ({"profile": "turbo"}, "hardware.profile"),
    ({"device": "gpu"}, "hardware.device"),
    ({"device": "mps:1"}, "hardware.device"),
    ({"encoder": "x265"}, "hardware.encoder"),
    ({"tts_engine_fallback": "f5"}, "tts_engine_fallback"),
])
def test_bad_values(cfg, msg):
    with pytest.raises(ConfigError, match=msg):
        H.resolve(cfg, hw_cpu())


def project(tmp_path, **over):
    raw = {"blocks": [{"text": t, "scene": {"type": "headline", "lines": ["x"]}} for t in BLOCKS], **over}
    return from_dict(raw, tmp_path)


def test_project_validation(tmp_path):
    assert project(tmp_path).cfg["hardware"]["profile"] == "auto"
    with pytest.raises(ConfigError, match="hardware.profile"):
        project(tmp_path, hardware={"profile": "max"})
    with pytest.raises(ConfigError, match="неизвестные поля"):
        project(tmp_path, hardware={"gpu": True})
    assert project(tmp_path, hardware={"device": "cuda:1"}).cfg["hardware"]["device"] == "cuda:1"


# --- подключение: util.device, Whisper, озвучка, музыка ------------------------------------------------------------
def test_device_and_whisper_settings_follow_profile(tmp_path):
    p = project(tmp_path)
    env = {}
    H.apply(p, hw=hw_cuda(24.0, n=2), env=env, quiet=True)
    assert util.device() == "cuda" and util.device("cpu") == "cpu"
    assert env["OMP_NUM_THREADS"] == "16"
    s = asr.settings(p.cfg["asr"])
    assert s == {"model": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "device": "cuda", "compute_type": "float16",
                 "device_index": 0}
    H.apply(p, hw=hw_cpu(6), env=env, quiet=True)
    assert util.device() == "cpu"
    assert asr.settings(p.cfg["asr"]) == {"model": "small", "device": "cpu", "compute_type": "int8",
                                          "cpu_threads": 6, "num_workers": 1}
    assert asr.settings({"model": "tiny", "device": "cpu"})["model"] == "tiny"  # свой asr.model важнее профиля
    H.apply(project(tmp_path, hardware={"whisper_model": "medium"}), hw=hw_cpu(), env=env, quiet=True)
    assert asr.settings({"model": "tiny"})["model"] == "medium"


def tone(path: Path, seconds: float = 1.0, sr: int = 22050) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(seconds * sr))
    return path


def test_voice_fallback_to_piper_in_pipeline(tmp_path, monkeypatch):
    p = project(tmp_path, voice={"engine": "f5", "voices": ["dmitri"], "seeds": [7]})
    monkeypatch.setattr(voice, "_f5_model", lambda: pytest.fail("F5 без видеокарты не грузится"))
    monkeypatch.setattr(voice, "piper_model", lambda pr, name: Path("m.onnx"))
    monkeypatch.setattr(voice, "_piper_tts", lambda text, model, out: tone(out))
    monkeypatch.setattr(asr, "transcribe", lambda path, cfg: p.script)
    H.apply(p, hw=hw_cpu(), env={}, quiet=True)
    choice = voice.synthesize(p)
    assert choice["fallback"] == "piper" and choice["requested"] == "f5" and choice["file"] == "takes/piper-dmitri.wav"
    assert util.read_json(p.voice_dir / "choice.json")["fallback"] == "piper"


def test_voice_no_fallback_outside_pipeline_or_with_allow_slow(tmp_path, monkeypatch):
    p = project(tmp_path, voice={"engine": "piper", "voices": ["dmitri"]})
    assert voice._engine(p) == ("piper", None)
    p = project(tmp_path, voice={"engine": "f5"})
    assert voice._engine(p) == ("f5", None)  # без apply — движок как в reel.yaml
    H.apply(project(tmp_path, voice={"engine": "f5"}, hardware={"allow_slow": True}), hw=hw_cpu(), env={},
            quiet=True)
    assert voice._engine(p) == ("f5", None)


def test_voice_fallback_without_piper_explains(tmp_path, monkeypatch):
    p = project(tmp_path, voice={"engine": "qwen3", "voices": ["dmitri"], "piper_models": "nowhere"})
    monkeypatch.delenv("PIPER_MODELS", raising=False)
    H.apply(p, hw=hw_cpu(), env={}, quiet=True)
    with pytest.raises(util.ToolError, match="allow_slow"):
        voice._engine(p)


def test_music_skipped_on_cpu_and_one_variant_on_lowvram(tmp_path, monkeypatch):
    p = project(tmp_path)
    monkeypatch.setattr(music, "_generate", lambda *a, **k: pytest.fail("MusicGen не должен запускаться"))
    H.apply(p, hw=hw_cpu(), env={}, quiet=True)
    assert music.make_music(p, 10.0) is None
    assert "skipped" in util.read_json(p.music_dir / "choice.json")

    calls = []

    def fake(prompt, seed, seconds, out, model="m"):
        calls.append(model)
        return tone(out, 2.0)

    monkeypatch.setattr(music, "_generate", fake)
    monkeypatch.setattr(music, "normalize", lambda src, dst: tone(dst, 2.0))
    monkeypatch.setattr(music, "dips", lambda f: [])
    monkeypatch.setattr(music, "extend", lambda path, seconds: path)
    monkeypatch.setattr(util, "wav_duration", lambda f: 2.0)
    H.apply(p, hw=hw_cuda(6.0), env={}, quiet=True)
    assert music.make_music(p, 10.0) == p.music_wav
    assert calls == ["facebook/musicgen-small"]
    calls.clear()
    H.apply(p, hw=hw_cuda(24.0), env={}, quiet=True)
    music.make_music(p, 10.0)
    assert len(calls) == 2


def test_video_codecs_fallback_order():
    a = H.resolve({}, hw_cuda())
    assert [n for n, _ in H.video_codecs(a)] == ["h264_nvenc", "libx264"]
    assert H.video_codecs(H.resolve({}, hw_cpu()))[0][1][:2] == ["-c:v", "libx264"]


def test_release_clears_model_caches(tmp_path):
    asr._MODELS["x"] = object()
    music._GEN["y"] = object()
    H.apply(project(tmp_path), hw=hw_cuda(24.0), env={}, quiet=True)
    H.after_stage("music")
    assert asr._MODELS and music._GEN  # gpu-max держит модели
    H.apply(project(tmp_path), hw=hw_cuda(6.0), env={}, quiet=True)
    H.after_stage("music")
    assert not asr._MODELS and not music._GEN


# --- doctor -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("make, profile", [
    (lambda: hw_cuda(24.0), "gpu-max"), (lambda: hw_cuda(6.0, "RTX 2060"), "gpu-lowvram"), (hw_mps, "apple"),
    (lambda: hw_cpu(4), "cpu-fast"), (lambda: hw_cpu(32, 128.0), "cpu-fast")])
def test_doctor_report_each_profile(make, profile, tmp_path):
    hw = make()
    rep = H.doctor_report(hw=hw, env={})
    assert f"■ Профиль: {profile}" in rep and "Оценка времени" in rep and "итого" in rep
    js = H.doctor_json(project(tmp_path), hw=hw, env={})
    assert js["accel"]["profile"] == profile and js["estimate_total"] > 0
    json.dumps(js)


def test_doctor_missing_tools_hints():
    hw = hw_cpu(ffmpeg=None, node=None, npx=None, packages=dict(PKGS, **{"faster-whisper": False}))
    rep = H.doctor_report(hw=hw, env={})
    assert "winget install Gyan.FFmpeg" in rep and "OpenJS.NodeJS.LTS" in rep and "pip install faster-whisper" in rep
    mac = H.doctor_report(hw=hw_mps(ffmpeg=None, node="v18.1.0"), env={})
    assert "brew install ffmpeg" in mac and "brew install node" in mac
    lin = H.missing(hw_cuda(ffmpeg=None))
    assert any("apt install ffmpeg" in how for _, how in lin)


def test_estimate_gpu_faster_than_cpu():
    tot = {}
    for name, hw, cfg in (("gpu", hw_cuda(), {}), ("cpu", hw_cpu(4), {"allow_slow": True})):
        a = H.resolve(cfg, hw)
        tot[name] = sum(t for _, t, _ in H.estimate(a, hw, "f5", "musicgen"))
    assert tot["gpu"] < tot["cpu"]
