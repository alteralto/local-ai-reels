"""Фоновая музыка: MusicGen / свой файл / без музыки. Громкость −18 LUFS, проверка провалов громкости.

Всё пишется в project.music_dir: variant-<i>.wav (MusicGen, уже −18 LUFS), choice.json, music.wav.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
from pathlib import Path

from . import util
from .project import Project

LOUDNORM = "loudnorm=I=-18:TP=-2:LRA=11"
MODEL = "facebook/musicgen-small"
_GEN: dict = {}


def normalize(src: Path, dst: Path) -> Path:
    """Громкость −18 LUFS, 48 кГц, стерео (как finish.sh music)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    util.run([util.ff(), "-y", "-v", "error", "-i", src, "-vn", "-af", LOUDNORM, "-ar", "48000", "-ac", "2", dst])
    return dst


def loudness(path: Path) -> float:
    """Интегральная громкость, LUFS (ffmpeg ebur128)."""
    p = util.run([util.ff(), "-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"])
    out = (p.stdout or "") + (p.stderr or "")
    tail = out[out.rfind("Summary:"):] if "Summary:" in out else out
    m = re.search(r"I:\s*(-?[\d.]+|-inf)\s*LUFS", tail)
    if not m:
        raise util.ToolError(f"ebur128: не нашёл громкость в выводе ffmpeg для {path}")
    return float(m.group(1))


def _pcm(path: Path, sr: int = 16000):
    """Звук моно float32 через ffmpeg (любой формат на входе)."""
    import numpy as np  # noqa: WPS433
    p = subprocess.run([util.ff(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True)
    if p.returncode != 0:
        raise util.ToolError(f"ffmpeg не прочитал {path}:\n{p.stderr.decode('utf-8', 'replace')[-1500:]}")
    return np.frombuffer(p.stdout, dtype=np.float32), sr


def dips(path: Path, win: float = 2.0, drop_db: float = 9.0) -> list[dict]:
    """Провалы громкости: окна по win с, где RMS тише медианы больше чем на drop_db дБ.

    Последнее неполное окно не считается. Возвращает [{"start", "end", "db", "drop"}].
    """
    import numpy as np  # noqa: WPS433
    x, sr = _pcm(path)
    n = int(win * sr)
    k = len(x) // n if n else 0
    if k == 0:
        return []
    rms = np.sqrt(np.mean(x[:k * n].astype(np.float64).reshape(k, n) ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-10)
    med = float(np.median(db))
    return [{"start": round(i * win, 2), "end": round((i + 1) * win, 2), "db": round(float(d), 1),
             "drop": round(med - float(d), 1)} for i, d in enumerate(db) if med - d > drop_db]


def extend(path: Path, seconds: float, xfade: float = 2.0) -> Path:
    """Трек короче seconds — повторяет его встык с перекрёстным затуханием и плавно гасит конец."""
    dur = util.wav_duration(path)
    if dur >= seconds:
        return path
    xf = min(xfade, dur / 4)
    n = max(2, math.ceil((seconds - xf) / (dur - xf)))
    chain, last = [], "0:a"
    for i in range(1, n):
        chain.append(f"[{last}][{i}:a]acrossfade=d={xf:.3f}:c1=tri:c2=tri[x{i}]")
        last = f"x{i}"
    fade = min(1.5, seconds / 4)
    chain.append(f"[{last}]atrim=0:{seconds:.3f},afade=t=out:st={seconds - fade:.3f}:d={fade:.3f}[out]")
    tmp = path.with_name(path.stem + ".ext.wav")
    cmd = [util.ff(), "-y", "-v", "error"]
    for _ in range(n):
        cmd += ["-i", path]
    cmd += ["-filter_complex", ";".join(chain), "-map", "[out]", "-ar", "48000", "-ac", "2", tmp]
    util.run(cmd)
    tmp.replace(path)
    util.say(f"музыка {dur:.1f} с короче ролика ({seconds:.1f} с): повторена {n} раз(а) с перекрёстным затуханием")
    return path


def _generate(prompt: str, seed: int, seconds: float, out: Path, model: str = MODEL) -> Path:
    """Один вариант MusicGen → wav (частота модели, моно float32). Модель грузится один раз."""
    try:
        import numpy as np  # noqa: WPS433
        import scipy.io.wavfile as wf  # noqa: WPS433
        import torch  # noqa: WPS433
        from transformers import AutoProcessor, MusicgenForConditionalGeneration  # noqa: WPS433
    except ImportError as e:
        raise util.ToolError("для MusicGen нужны torch, transformers и scipy: pip install torch transformers scipy") from e
    if model not in _GEN:
        dev = util.device()
        util.say(f"MusicGen: загружаю {model} ({dev}; на процессоре — несколько минут на вариант)")
        _GEN.clear()
        _GEN[model] = (AutoProcessor.from_pretrained(model),
                       MusicgenForConditionalGeneration.from_pretrained(model).to(dev), dev)
    proc, m, dev = _GEN[model]
    fps = getattr(m.config.audio_encoder, "frame_rate", 50) or 50
    tokens = min(1500, int(seconds * fps) + 100)  # MusicGen держит ~30 с; длиннее — extend()
    x = proc(text=[prompt], padding=True, return_tensors="pt").to(dev)
    torch.manual_seed(seed)
    a = m.generate(**x, do_sample=True, guidance_scale=3, max_new_tokens=tokens)
    out.parent.mkdir(parents=True, exist_ok=True)
    wf.write(str(out), m.config.audio_encoder.sampling_rate, a[0, 0].cpu().numpy().astype(np.float32))
    return out


def make_music(project: Project, seconds: float) -> Path | None:
    """music.wav на −18 LUFS длиной не меньше seconds; engine none → None.

    musicgen: по варианту на каждый промпт (seed = music.seed + i), выбирается вариант с наименьшим числом
    и глубиной провалов громкости (при равенстве — первый); всё — в choice.json.
    """
    c = project.cfg["music"]
    if c["engine"] == "none":
        return None
    out = project.music_wav
    if c["engine"] == "file":
        src = project.path(c["file"])
        if not src or not src.is_file():
            raise util.ToolError(f"music.file: нет файла {src}")
        normalize(src, out)
        return extend(out, seconds)
    prompts = c["prompts"] if isinstance(c["prompts"], list) else [c["prompts"]]
    if not prompts:
        raise util.ToolError("music.prompts: нужен хотя бы один промпт")
    variants = []
    for i, prompt in enumerate(prompts):
        seed = int(c["seed"]) + i
        util.say(f"MusicGen, вариант {i}: {prompt}")
        raw = _generate(prompt, seed, seconds, project.music_dir / f"variant-{i}.raw.wav", c.get("model") or MODEL)
        f = normalize(raw, project.music_dir / f"variant-{i}.wav")
        raw.unlink(missing_ok=True)
        d = dips(f)
        variants.append({"file": f.name, "prompt": prompt, "seed": seed, "duration": round(util.wav_duration(f), 2),
                         "dips": d})
        util.say(f"  провалов громкости: {len(d)}" + "".join(f"; {x['start']}–{x['end']} с −{x['drop']} дБ" for x in d))
    best = min(range(len(variants)), key=lambda j: (len(variants[j]["dips"]),
                                                     sum(x["drop"] for x in variants[j]["dips"]), j))
    shutil.copyfile(project.music_dir / variants[best]["file"], out)
    extend(out, seconds)
    util.write_json(project.music_dir / "choice.json", {"file": variants[best]["file"], "index": best,
                                                        "variants": variants})
    if variants[best]["dips"]:
        util.say(f"ВНИМАНИЕ: во всех вариантах есть провалы громкости; взят {variants[best]['file']} — "
                 "проверьте на слух или поменяйте music.seed", err=True)
    else:
        util.say(f"музыка: {variants[best]['file']}")
    return out


__all__ = ["make_music", "dips", "loudness", "normalize", "extend"]
