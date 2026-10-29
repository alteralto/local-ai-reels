"""Таймкоды: слова сценария ↔ Whisper, границы блоков, раскладка блоков на таймлайне монтажа, якоря анимации.

Все слова здесь — словари {"w": текст, "s": начало, "e": конец} в секундах.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .project import Project, word_count


def script_tokens(text: str) -> list[str]:
    """Слова сценария в том виде, в каком их покажут субтитры: тире и одиночные знаки висят на предыдущем слове."""
    tokens: list[str] = []
    for tok in text.split():
        if re.search(r"\w", tok) or not tokens:
            tokens.append(tok)
        else:
            tokens[-1] += " " + tok
    return tokens


def merge_heard(words: list[dict]) -> list[dict]:
    """Whisper режет «Санкт-Паули» на «Санкт» + «-Паули», тире распознаёт отдельным словом — склеиваем."""
    heard: list[dict] = []
    for w in words:
        if heard and (w["w"].startswith("-") or not re.search(r"\w", w["w"])):
            heard[-1] = {**heard[-1], "w": heard[-1]["w"] + ("" if w["w"].startswith("-") else " ") + w["w"],
                         "e": w["e"]}
        else:
            heard.append(dict(w))
    return heard


def aligned(words: list[dict], script: str) -> list[dict]:
    """Субтитры = слова сценария с таймкодами Whisper (пунктуация и регистр — из сценария)."""
    heard = merge_heard(words)
    tokens = script_tokens(script)
    if len(tokens) != len(heard):
        raise ValueError(f"сценарий {len(tokens)} слов, распознано {len(heard)} — озвучка разошлась с текстом")
    return [{**h, "w": t} for h, t in zip(heard, tokens)]


def block_cuts(words: list[dict], counts: list[int], bias: float = 0.12) -> list[float]:
    """Границы блоков в мастер-файле по числу слов в каждом блоке.

    words — слова сценария с таймкодами (после aligned), counts — число слов в блоках (как script_tokens).
    Граница — середина паузы между последним словом блока и первым словом следующего минус bias;
    первая — 0, последняя — конец речи плюс bias. Возвращает len(counts)+1 чисел.
    """
    if sum(counts) != len(words):
        raise ValueError(f"в блоках {sum(counts)} слов, в таймкодах {len(words)}")
    cuts, i = [0.0], 0
    for n in counts[:-1]:
        i += n
        a, b = words[i - 1], words[i]
        cut = (a["e"] + b["s"]) / 2 - bias
        cuts.append(round(min(max(cut, a["e"] - 0.05), b["s"]), 3))
    cuts.append(round(words[-1]["e"] + bias, 3))
    return cuts


def block_token_counts(project: Project) -> list[int]:
    return [len(script_tokens(b.text)) for b in project.blocks]


@dataclass
class Placement:
    block: int          # номер блока, с 1
    start: float        # начало блока на таймлайне монтажа, с
    length: float       # длина речи блока, с
    media_start: float  # откуда брать звук блока в мастер-файле, с


def place(order: list[int], cuts: list[float], gap: float = 0.12, tail: float = 1.5) -> tuple[list[Placement], float]:
    """Раскладка блоков монтажа: куски мастер-файла встык с паузой gap, в конце — финальная карточка tail.
    Возвращает (раскладка в порядке монтажа, общая длительность)."""
    out, t = [], 0.0
    for b in order:
        length = cuts[b] - cuts[b - 1]
        out.append(Placement(b, round(t, 3), round(length, 3), cuts[b - 1]))
        t += length + gap
    return out, round(t - gap + tail, 2)


def _words_norm(s: str) -> list[str]:
    return re.sub(r"[^\w ]", " ", s.lower().replace("ё", "е").replace("+", "")).split()


def anchor(block_words: list[dict], at, default: float = 0.0) -> float:
    """Время якоря анимации от начала блока, с.

    block_words — слова блока с таймкодами ОТ НАЧАЛА БЛОКА; at:
      None            → default
      число           → это число (секунды от начала блока)
      "фраза"         → начало первого вхождения фразы (без учёта регистра, ё/е и пунктуации)
      "фраза@end"     → конец последнего слова фразы
      "фраза+0.3"     → со сдвигом (можно и "-0.2", и вместе с @end: "фраза@end+0.1")
      "50%"           → доля длины речи блока
    Не нашлась фраза — ValueError (опечатку в якоре лучше увидеть на сборке, чем в ролике).
    """
    if at is None or at == "":
        return default
    if isinstance(at, (int, float)):
        return float(at)
    s = str(at).strip()
    m = re.fullmatch(r"(.*?)([+-]\d+(?:\.\d+)?)?", s)
    body, shift = m.group(1).strip(), float(m.group(2) or 0)
    if body.endswith("%") and re.fullmatch(r"\d+(\.\d+)?%", body):
        end = block_words[-1]["e"] if block_words else 0.0
        return round(end * float(body[:-1]) / 100 + shift, 3)
    use_end = body.endswith("@end")
    if use_end:
        body = body[:-4].strip()
    try:
        return round(float(body) + shift, 3)
    except ValueError:
        pass
    need = _words_norm(body)
    have = [_words_norm(w["w"]) for w in block_words]
    flat = [(i, x) for i, ws in enumerate(have) for x in ws]
    for k in range(len(flat) - len(need) + 1):
        if [x for _, x in flat[k:k + len(need)]] == need:
            i0, i1 = flat[k][0], flat[k + len(need) - 1][0]
            return round((block_words[i1]["e"] if use_end else block_words[i0]["s"]) + shift, 3)
    raise ValueError(f"якорь «{at}» не найден в словах блока: {' '.join(w['w'] for w in block_words)}")


def block_words(words: list[dict], cuts: list[float], block: int) -> list[dict]:
    """Слова блока с таймкодами от начала блока."""
    a, z = cuts[block - 1], cuts[block]
    return [{**w, "s": round(w["s"] - a, 3), "e": round(w["e"] - a, 3)} for w in words if a <= w["s"] < z]


__all__ = ["script_tokens", "merge_heard", "aligned", "block_cuts", "block_token_counts", "Placement", "place",
           "anchor", "block_words", "word_count"]
