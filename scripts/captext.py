"""Субтитры = слова сценария с таймкодами Whisper (пунктуация и регистр — из сценария, не из распознавания)."""
import re


def aligned(words, script):
    heard = []
    for w in words:  # Whisper режет «Санкт-Паули» на «Санкт» + «-Паули» — склеиваем
        if heard and (w["w"].startswith("-") or not re.search(r"\w", w["w"])):  # «-Паули», отдельное «–»
            heard[-1] = {**heard[-1], "w": heard[-1]["w"] + ("" if w["w"].startswith("-") else " ") + w["w"], "e": w["e"]}
        else:
            heard.append(dict(w))
    tokens = []
    for tok in script.split():
        if re.search(r"\w", tok) or not tokens:
            tokens.append(tok)
        else:
            tokens[-1] += " " + tok  # тире висит на предыдущем слове
    if len(tokens) != len(heard):
        raise SystemExit(f"сценарий {len(tokens)} слов, распознано {len(heard)} — проверь озвучку")
    return [{**h, "w": t} for h, t in zip(heard, tokens)]
