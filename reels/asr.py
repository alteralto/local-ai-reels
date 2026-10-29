"""Распознавание речи faster-whisper: текст, пословные таймкоды, сверка с текстом сценария.

cfg — раздел asr из reel.yaml: {"model": <имя или папка модели>, "device": "auto" | "cuda" | "cpu"}.
Модель, тип вычислений и потоки уточняет профиль железа (reels.hardware, docs/hardware.md).
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import util

_MODELS: dict[tuple, object] = {}


def settings(cfg: dict) -> dict:
    """Модель и параметры WhisperModel по разделу asr и профилю железа (reels.hardware).

    Модель: hardware.whisper_model (если задан) > asr.model (если отличается от умолчания) > выбор профиля.
    Устройство: asr.device (если не auto) > профиль (CTranslate2 умеет только CUDA и CPU).
    """
    from . import hardware  # noqa: WPS433
    from .project import DEFAULTS  # noqa: WPS433
    acc = hardware.current(deep=False)
    default = DEFAULTS["asr"]["model"]
    model = cfg.get("model") or default
    if acc.whisper_pinned:
        model = acc.whisper_model
    elif model == default and acc.whisper_model:
        model = acc.whisper_model
    pref = str(cfg.get("device") or "auto")
    dev, index = (acc.whisper_device, acc.cuda_device) if pref == "auto" else (pref, acc.cuda_device)
    if dev.startswith("cuda:"):
        dev, index = "cuda", int(dev.split(":", 1)[1])
    if dev == acc.whisper_device:
        compute = acc.whisper_compute
    else:
        compute = "float16" if dev == "cuda" else "int8"
    kw = {"device": dev, "compute_type": compute}
    if dev == "cuda":
        kw["device_index"] = index
    else:
        kw.update(cpu_threads=acc.whisper_threads, num_workers=acc.whisper_workers)
    return {"model": model, **kw}


def load(cfg: dict):
    """Модель Whisper (кэшируется по модели и параметрам). Настройки — settings(cfg)."""
    s = settings(cfg)
    key = tuple(sorted(s.items()))
    if key not in _MODELS:
        try:
            from faster_whisper import WhisperModel  # noqa: WPS433
        except ImportError as e:
            raise util.ToolError("нет faster-whisper: pip install faster-whisper") from e
        model = s.pop("model")
        util.say(f"Whisper: загружаю {model} ({s['device']}, {s['compute_type']})")
        _MODELS[key] = WhisperModel(model, **s)
    return _MODELS[key]


def available() -> bool:
    """Есть ли faster-whisper (без загрузки модели)."""
    try:
        import faster_whisper  # noqa: F401, WPS433
        return True
    except ImportError:
        return False


def transcribe(path: str | Path, cfg: dict) -> str:
    """Распознанный текст файла (сегменты через пробел)."""
    segs, _ = load(cfg).transcribe(str(path), language="ru")
    return " ".join(s.text.strip() for s in segs).strip()


def words(path: str | Path, cfg: dict) -> list[dict]:
    """Пословные таймкоды: [{"w": слово, "s": начало, "e": конец}], секунды с точностью 3 знака."""
    segs, _ = load(cfg).transcribe(str(path), language="ru", word_timestamps=True)
    return [{"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
            for s in segs for w in (s.words or []) if w.word.strip()]


def normalize(text: str) -> list[str]:
    """Слова для сверки: без «+», строчные, ё→е, только буквы."""
    return re.sub(r"[^a-zа-я ]", " ", text.replace("+", "").lower().replace("ё", "е")).split()


def ratio(script: str, heard: str) -> float:
    """Похожесть сценария и распознанного текста по словам (difflib), 0…1."""
    return round(difflib.SequenceMatcher(None, normalize(script), normalize(heard)).ratio(), 3)


__all__ = ["load", "settings", "available", "transcribe", "words", "normalize", "ratio"]
