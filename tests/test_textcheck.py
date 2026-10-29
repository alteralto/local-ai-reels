"""Правила Instagram в проверке сценария (reels.textcheck): крючок, призыв, длина, планы, текст в кадре, зоны."""
from __future__ import annotations

from pathlib import Path

from reels import textcheck as TC
from reels.project import from_dict

BODY = [
    "Общественный туалет кажется мелочью, но без него город сжимается до пары кварталов.",
    "Королевское общество здравоохранения выяснило: каждый пятый выходит из дома реже, чем хочет.",
    "Кабинка прячет свободу: без неё город кончается за углом.",
    "Отправь тому, кто рисует ваш город.",
]


def make(hook: str, body=BODY, scenes=None, **cfg):
    texts = [hook, *body]
    blocks = [{"text": t, "scene": (scenes or {}).get(i, {"type": "headline", "lines": ["Город"]})}
              for i, t in enumerate(texts, 1)]
    return from_dict({"blocks": blocks, "source": "RSPH, 2019", "limit": 90,
                      "edits": {"linear": "".join(str(i) for i in range(1, len(texts) + 1))}, **cfg}, Path("."))


def warnings(project) -> str:
    return "\n".join(str(i) for i in TC.check_script(project) if i.level == "warning")


def test_good_reel_has_no_instagram_warnings():
    text = warnings(make("Что прячет обычная городская кабинка?"))
    for word in ("крючок", "призыв", "план", "безопасной", "знаков", "для Reels"):
        assert word not in text, text


def test_slow_opener_long_and_weak_hook():
    assert "медленное начало «сегодня я расскажу»" in warnings(make("Сегодня я расскажу про туалеты в городе."))
    assert "медленное начало «привет»" in warnings(make("Привет! Поговорим о туалетах."))
    assert "слабый крючок" in warnings(make("Туалеты бывают в парках и на вокзалах."))
    long = warnings(make("Что прячет обычная городская кабинка на углу тихой улицы рядом с парком и вокзалом?"))
    assert "крючок 14 слов" in long and "(крючок в linear)" in long
    assert TC.hook_problems("Каждый пятый горожанин сидит дома.") == []
    assert TC.hook_problems("Туалет — тоже архитектура.") == [], "тире-противопоставление — крючок"


def test_missing_cta_and_cta_in_scene_or_endcard():
    body = BODY[:-1] + ["Город для всех начинается с кабинки."]
    assert "нет призыва к действию" in warnings(make("Что прячет кабинка?", body))
    assert "нет призыва" not in warnings(make("Что прячет кабинка?", body, endcard="Заходите *оценивать*"))
    sc = {5: {"type": "headline", "lines": ["Город"], "cta": "сохрани, чтобы не потерять"}}
    assert "нет призыва" not in warnings(make("Что прячет кабинка?", body, scenes=sc))
    assert TC.has_cta("Ссылка в профиле") and not TC.has_cta("Спасибо за внимание")


def test_cta_and_hook_checked_per_edit():
    body = BODY[:-1] + ["Сохрани видео себе."]
    p = make("Что прячет обычная городская кабинка?", body, edits={"linear": "12345", "from-end": "51234"})
    text = warnings(p)
    assert "блок 5 «Блок 5» (крючок в from-end)" in text, "в монтаже «с конца» первым стоит призыв"
    assert "блок 4 «Блок 4» (финал в from-end)" in text


def test_duration_estimate():
    short = make("Что прячет кабинка?", body=["Отправь другу."])
    assert "для Reels лучше 15–45 с" in warnings(short)
    assert TC.seconds(130) > 45


def test_long_static_shot_and_shot_lengths():
    text = BODY[1]
    two = {"shots": [{"type": "headline", "lines": ["1 из 5"]},
                     {"type": "headline", "lines": ["дома"], "at": "реже"}]}
    lengths = TC.shot_lengths(text, two)
    assert len(lengths) == 2 and abs(sum(lengths) - TC.seconds(12)) < 1e-6
    late = {"shots": [{"type": "headline", "lines": ["1 из 5"]},
                      {"type": "headline", "lines": ["дома"], "at": "хочет"}]}
    assert TC.shot_lengths(text, late)[0] > 4
    w = warnings(make("Что прячет кабинка?", scenes={3: late}))
    assert "план 1 держится" in w and "блок 3" in w
    assert "план 1 держится" not in warnings(make("Что прячет кабинка?", scenes={3: two}))
    no_at = {"shots": [{"type": "headline", "lines": ["a"]}, {"type": "headline", "lines": ["b"]}]}
    assert TC.shot_lengths(text, no_at) == [TC.seconds(12) / 2] * 2, "без якоря — равные доли, как в compose"


def test_shots_types_and_anchors_are_errors():
    sc = {2: {"shots": [{"type": "headline", "lines": ["a"]}, {"type": "nope", "at": "метро"}]}}
    errors = [str(i) for i in TC.check_script(make("Что прячет кабинка?", scenes=sc)) if i.level == "error"]
    assert any("«nope»" in e for e in errors) and any("якорь «метро»" in e for e in errors)


def test_sticker_length_and_safe_zones():
    long_line = {"type": "headline", "lines": ["x"]}
    assert TC.sticker_lines({"type": "stickers", "lines": [{"text": "Что *прячет*"}, "кабинка?"]}) == \
        ["Что прячет", "кабинка?"]
    assert TC.sticker_lines({"shots": [long_line, {"type": "tag", "text": "тук-тук"}]}) == ["тук-тук"]
    sc = {1: {"type": "headline", "lines": ["Город"], "top": 120}}
    w = warnings(make("Что прячет кабинка?", scenes=sc))
    assert "top: 120 — по вертикали вне безопасной зоны 250…1500" in w
    assert TC.safe_zone_problems({"type": "tag", "text": "x", "x": 1000, "y": 900})[0].startswith("x: 1000")
    assert TC.safe_zone_problems({"type": "tag", "text": "x", "y": 0.9})  # доля кадра: 0.9 × 1920 > 1500
    assert TC.safe_zone_problems({"type": "map", "points": [{"x": 880, "y": 100}]}) == [], "координаты карты — свои"
    lines = {"type": "stickers", "lines": [{"text": "a", "y": 1700}]}
    assert TC.safe_zone_problems({"shots": [lines]}) == [
        "shots[0].lines[0].y: 1700 — по вертикали вне безопасной зоны 250…1500 px (сверху и снизу кадр закрывает "
        "интерфейс Instagram)"]
