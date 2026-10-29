"""Библиотека форматов коротких роликов (reels.formats): целостность и описание для LLM."""
from __future__ import annotations

import pytest

from reels import formats as F


def test_formats_integrity():
    assert {"question", "myth", "list", "twist", "before-after", "promo", "news"} <= set(F.FORMATS)
    for name, f in F.FORMATS.items():
        assert f.name == name and f.title and f.when and f.loop
        roles = [b.role for b in f.beats]
        assert roles[0] == "hook" and roles[-1] == "cta", name
        assert set(roles) <= set(F.ROLES) and "alt-hook" not in roles
        assert f.hook.count == (1, 1) and f.hook.words[1] <= 10, "крючок — один блок до 10 слов"
        assert f.beat("cta").words[1] <= 8 and f.beat("alt-hook") is f.hook
        for b in f.beats:
            assert 0 <= b.count[0] <= b.count[1] and 0 < b.words[0] <= b.words[1] and b.seconds[0] <= b.seconds[1]
            assert b.shots and b.goal
        assert set(f.hooks) <= set(F.HOOKS) and set(f.cta) <= set(F.CTA)
        lo, hi = F.body_blocks(f)
        assert lo >= 3 and hi + 3 <= 9, "три крючка + тело помещаются в 9 блоков (номер блока — одна цифра)"
        assert f.seconds[0] >= F.REEL_SECONDS[0] and f.seconds[1] <= F.REEL_SECONDS[1]


def test_get_aliases_and_errors():
    assert F.get("question") is F.get("Вопрос-загадка") is F.get(" вопрос ")
    assert F.get("brag").name == "promo" and F.get("до/после").name == "before-after"
    with pytest.raises(KeyError, match="нет формата"):
        F.get("опера")


def test_describe_markdown():
    md = F.describe()
    for f in F.FORMATS.values():
        assert f"### {f.name} — {f.title}" in md
    one = F.describe(["myth"], registry={"stat": None, "stickers": None})
    assert "### myth" in one and "### question" not in one
    assert "| hook |" in one and "| cta |" in one and "stickers" in one and "Закольцовка" in one
    assert "«Что прячет обычная городская кабинка?»" in F.describe_hooks()


def test_available_shots_follow_registry():
    beat = F.FORMATS["question"].hook
    assert F.available_shots(beat, {"stickers": 1, "cover": 1}) == ["cover", "stickers"]
    assert F.available_shots(beat, {"headline": 1, "stat": 1, "list": 1}) == ["headline", "stat"]
    assert F.available_shots(F.CTA_BEAT, {"list": 1}) == ["list"], "нет ни одного шаблона плана — текстовые"


def test_cta_words_and_openers_are_lowercase():
    assert all(w == w.lower() for w in F.CTA_WORDS + F.SLOW_OPENERS)
    assert {"сохрани", "отправь", "заходите", "подпишись"} <= set(F.CTA_WORDS)
