"""Распознавание речи faster-whisper: текст, пословные таймкоды, сверка с текстом сценария.

cfg — раздел asr из reel.yaml: {"model": <имя или папка модели>, "device": "auto" | "cuda" | "cpu"}.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import util

_MODELS: dict[tuple, object] = {}


def load(cfg: dict):
    """Модель Whisper (кэшируется по model+device). На видеокарте float16, на процессоре int8."""
    model = cfg.get("model") or "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    dev = util.device(cfg.get("device", "auto"))
    key = (model, dev)
    if key not in _MODELS:
        try:
            from faster_whisper import WhisperModel  # noqa: WPS433
        except ImportError as e:
            raise util.ToolError("нет faster-whisper: pip install faster-whisper") from e
        util.say(f"Whisper: загружаю {model} ({dev})")
        _MODELS[key] = WhisperModel(model, device=dev, compute_type="float16" if dev == "cuda" else "int8")
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


__all__ = ["load", "available", "transcribe", "words", "normalize", "ratio"]
