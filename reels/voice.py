"""Озвучка: ударения (RUAccent), эталоны голоса (Piper), дубли (F5-TTS / Piper / готовый файл), отбор дубля.

Всё пишется в project.voice_dir:
    accents.json   {"master": сценарий с «+», "ref": текст эталона с «+»}
    takes/         дубли
    takes.json     дубли со сверкой Whisper, лучший первым
    choice.json    выбранный дубль {file, voice, seed, ratio, ok}
    master.wav     копия выбранного дубля
    words.json     пословные таймкоды мастер-файла
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import wave
from pathlib import Path

from . import asr, util
from .project import Project

PAD = 0.45  # тишина в конце эталона: без неё хвост эталона просачивается в начало синтеза


# --- ударения ---------------------------------------------------------------
def _ruaccent():
    try:
        from ruaccent import RUAccent  # noqa: WPS433
    except ImportError as e:
        raise util.ToolError("нет RUAccent: pip install ruaccent") from e
    workdir = os.environ.get("RUACCENT_DIR", "models/ruaccent")
    util.say(f"RUAccent: загружаю turbo3.1 + словарь ({workdir})")
    acc = RUAccent()
    acc.load(omograph_model_size="turbo3.1", use_dictionary=True, workdir=workdir)
    return acc


def apply_accents(text: str, accents: dict) -> str:
    """Ручные правки ударений: целые слова, без учёта регистра и уже стоящих «+»; заглавная буква сохраняется."""
    for word, fixed in (accents or {}).items():
        plain = str(word).replace("+", "")
        if not plain:
            continue
        pat = r"(?<![\w+])\+?" + r"\+?".join(re.escape(c) for c in plain) + r"(?![\w+])"

        def repl(m: re.Match, fixed=str(fixed)) -> str:
            src = m.group(0).lstrip("+")
            if src[:1].isupper():
                i = len(fixed) - len(fixed.lstrip("+"))
                return fixed[:i] + fixed[i:i + 1].upper() + fixed[i + 1:]
            return fixed

        text = re.sub(pat, repl, text, flags=re.IGNORECASE)
    return text


def accent(project: Project) -> Path:
    """accents.json: для f5 — разметка RUAccent + ручные правки; для piper/file — текст как есть (Piper не читает «+»)."""
    v = project.cfg["voice"]
    master, ref = project.script, " ".join(str(v["ref_text"]).split())
    if v["engine"] == "f5":
        acc = _ruaccent()
        master, ref = acc.process_all(master), acc.process_all(ref)
        master, ref = apply_accents(master, v.get("accents")), apply_accents(ref, v.get("accents"))
    out = util.write_json(project.voice_dir / "accents.json", {"master": master, "ref": ref})
    util.say(f"ударения: {master}")
    return out


def _accents(project: Project) -> dict:
    return util.read_json(project.voice_dir / "accents.json") or util.read_json(accent(project))


# --- Piper ------------------------------------------------------------------
def piper_model(project: Project, voice: str) -> Path:
    """ru_RU-<голос>-medium.onnx из voice.piper_models или PIPER_MODELS (рядом должен лежать .onnx.json)."""
    dirs = [project.path(project.cfg["voice"].get("piper_models"))]
    if os.environ.get("PIPER_MODELS"):
        dirs.append(Path(os.environ["PIPER_MODELS"]))
    names = [f"ru_RU-{voice}-medium.onnx", f"{voice}.onnx", voice]
    for d in filter(None, dirs):
        for n in names:
            if (d / n).is_file() and n.endswith(".onnx"):
                return d / n
    raise util.ToolError(
        f"нет голоса Piper ru_RU-{voice}-medium.onnx (+ .onnx.json) в {', '.join(str(d) for d in dirs if d)}: "
        f"скачайте из rhasspy/piper-voices (ru/ru_RU/{voice}/medium/) или задайте PIPER_MODELS")


def _piper_tts(text: str, model: Path, out: Path) -> Path:
    """Синтез Piper: Python API пакета piper-tts, без него — команда piper."""
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        from piper import PiperVoice  # noqa: WPS433
    except ImportError:
        exe = shutil.which("piper")
        if not exe:
            raise util.ToolError("нет Piper: pip install piper-tts") from None
        p = subprocess.run([exe, "--model", str(model), "--output_file", str(out)], input=text, text=True,
                           encoding="utf-8", capture_output=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        if p.returncode != 0 or not out.exists():
            raise util.ToolError(f"piper завершился с кодом {p.returncode}:\n{(p.stderr or '')[-2000:]}")
        return out
    pv = PiperVoice.load(str(model))
    with wave.open(str(out), "wb") as wf:
        if hasattr(pv, "synthesize_wav"):
            pv.synthesize_wav(text, wf)
        else:  # piper-tts < 1.3
            pv.synthesize(text, wf)
    return out


def ensure_refs(project: Project) -> dict[str, Path]:
    """Эталоны голосов для F5: файл из voice.ref; нет — Piper читает ref_text, + 0,45 с тишины, 24 кГц моно."""
    v = project.cfg["voice"]
    refs = {}
    for name in v["voices"]:
        ref = project.path(v["ref"].format(voice=name))
        if not ref.exists():
            util.say(f"эталон {name}: синтез Piper → {ref}")
            raw = _piper_tts(" ".join(str(v["ref_text"]).split()), piper_model(project, name),
                             project.voice_dir / "refs" / f"ref-{name}-raw.wav")
            ref.parent.mkdir(parents=True, exist_ok=True)
            util.run([util.ff(), "-y", "-v", "error", "-i", raw, "-af", f"apad=pad_dur={PAD}",
                      "-ar", "24000", "-ac", "1", ref])
        refs[name] = ref
    return refs


# --- F5-TTS -----------------------------------------------------------------
def _f5_model():
    os.environ.setdefault("WANDB_MODE", "disabled")
    try:
        from f5_tts.api import F5TTS  # noqa: WPS433
    except ImportError as e:
        raise util.ToolError("нет F5-TTS: pip install f5-tts") from e
    m = Path(os.environ.get("TTS_MODELS", "models"))
    ckpt = m / "f5-ru/F5TTS_v1_Base_v2/model_last_inference.safetensors"
    vocab = m / "f5-ru/F5TTS_v1_Base/vocab.txt"
    for f in (ckpt, vocab):
        if not f.is_file():
            raise util.ToolError(f"нет {f}: скачайте веса Misha24-10/F5-TTS_RUSSIAN в {m / 'f5-ru'} "
                                 "или задайте папку моделей в TTS_MODELS")
    dev = util.device()
    util.say(f"F5-TTS: загружаю {ckpt} ({dev})")
    return F5TTS(model="F5TTS_v1_Base", ckpt_file=str(ckpt), vocab_file=str(vocab), device=dev,
                 hf_cache_dir=str(m / "hf"))


# --- дубли и отбор ------------------------------------------------------------
def _take(project: Project, f: Path, voice: str | None, seed: int | None, verify: bool = True) -> dict:
    t = {"file": f.relative_to(project.voice_dir).as_posix(), "voice": voice, "seed": seed,
         "ratio": None, "heard": None, "duration": round(util.wav_duration(f), 3)}
    if verify:
        heard = asr.transcribe(f, project.cfg["asr"])
        t.update(ratio=asr.ratio(project.script, heard), heard=heard)
        util.say(f"  {t['ratio']}  {t['file']}  {heard}")
    return t


def _takes_f5(project: Project, takes_dir: Path) -> list[dict]:
    v = project.cfg["voice"]
    acc, refs = _accents(project), ensure_refs(project)
    tts = _f5_model()
    out = []
    for name in v["voices"]:
        for seed in v["seeds"]:
            f = takes_dir / f"f5-{name}-{seed}.wav"
            util.say(f"дубль F5: {name}, seed {seed}")
            tts.infer(str(refs[name]), acc["ref"], acc["master"], file_wave=str(f), speed=v["speed"],
                      nfe_step=v["nfe_step"], seed=seed, show_info=lambda *a, **k: None)
            out.append(_take(project, f, name, seed))
    return out


def _takes_piper(project: Project, takes_dir: Path) -> list[dict]:
    out = []
    for name in project.cfg["voice"]["voices"]:
        util.say(f"дубль Piper: {name}")
        f = _piper_tts(project.script, piper_model(project, name), takes_dir / f"piper-{name}.wav")
        out.append(_take(project, f, name, None))
    return out


def _takes_file(project: Project, takes_dir: Path) -> list[dict]:
    src = project.path(project.cfg["voice"]["file"])
    if not src or not src.is_file():
        raise util.ToolError(f"voice.file: нет файла {src}")
    f = takes_dir / "file.wav"
    util.run([util.ff(), "-y", "-v", "error", "-i", src, "-vn", "-ar", "48000", "-c:a", "pcm_s16le", f])
    verify = asr.available()
    if not verify:
        util.say("сверка Whisper пропущена: нет faster-whisper (pip install faster-whisper)", err=True)
    try:
        return [_take(project, f, None, None, verify=verify)]
    except Exception as e:  # noqa: BLE001 — модель Whisper не скачалась и т. п.: свою озвучку принимаем без сверки
        if not verify:
            raise
        util.say(f"сверка Whisper пропущена: {str(e).splitlines()[0][:200]}", err=True)
        return [_take(project, f, None, None, verify=False)]


def synthesize(project: Project) -> dict:
    """Дубли выбранным движком, сверка Whisper, лучший дубль → master.wav. Возвращает choice.json.

    Лучший — с наибольшей сверкой; при равенстве — раньше по порядку голоса × seeds.
    Сверка ниже voice.min_ratio — дубль всё равно берётся, но с "ok": false и предупреждением.
    """
    v = project.cfg["voice"]
    takes_dir = project.voice_dir / "takes"
    if takes_dir.exists():
        shutil.rmtree(takes_dir)
    takes_dir.mkdir(parents=True)
    takes = {"f5": _takes_f5, "piper": _takes_piper, "file": _takes_file}[v["engine"]](project, takes_dir)
    takes = sorted(takes, key=lambda t: -(t["ratio"] if t["ratio"] is not None else 1.0))  # sorted устойчив
    util.write_json(project.voice_dir / "takes.json", takes)
    best = takes[0]
    ok = best["ratio"] is None or best["ratio"] >= v["min_ratio"]
    shutil.copyfile(project.voice_dir / best["file"], project.master_wav)
    choice = {"file": best["file"], "voice": best["voice"], "seed": best["seed"], "ratio": best["ratio"], "ok": ok}
    util.write_json(project.voice_dir / "choice.json", choice)
    if ok:
        util.say(f"выбран дубль {best['file']} (сверка {best['ratio']})")
    else:
        util.say(f"ВНИМАНИЕ: лучший дубль {best['file']} — сверка {best['ratio']} ниже {v['min_ratio']}: "
                 f"Whisper слышит «{best['heard']}». Поправьте ударения/текст или добавьте seeds.", err=True)
    return choice


def timestamps(project: Project) -> Path:
    """words.json мастер-файла: готовые таймкоды (engine file + voice.words) или Whisper."""
    v = project.cfg["voice"]
    src = project.path(v.get("words")) if v["engine"] == "file" else None
    if src:
        words = util.read_json(src)
        if not isinstance(words, list) or not all({"w", "s", "e"} <= set(w) for w in words):
            raise util.ToolError(f"voice.words: {src} — ожидался список {{\"w\", \"s\", \"e\"}}")
    else:
        if not project.master_wav.exists():
            raise util.ToolError(f"нет {project.master_wav}: сначала озвучка (synthesize)")
        words = asr.words(project.master_wav, project.cfg["asr"])
    util.say(f"таймкоды: {len(words)} слов")
    return util.write_json(project.words_json, words)


__all__ = ["accent", "apply_accents", "ensure_refs", "piper_model", "synthesize", "timestamps"]
