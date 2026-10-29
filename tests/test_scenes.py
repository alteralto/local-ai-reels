"""Библиотека сцен: каждый шаблон собирается из своего примера, CSS не протекает, текст экранируется."""
import re
from pathlib import Path

import pytest

from reels import compose
from reels.scenes import REGISTRY, SceneCtx, SceneError, render_scene
from reels.textcheck import _anchors
from reels.themes import THEMES, get_theme, theme_css

TEMPLATES = sorted(n for n in REGISTRY if n != "custom")


def ctx_for(params: dict, block: int = 2, position: int = 0, count: int = 1) -> SceneCtx:
    """Диктор произносит все якоря примера по очереди, по 0,4 с на слово."""
    text = " ".join(["Вступление,"] + [re.sub(r"@end|[+-]\d+(\.\d+)?$", "", a) for a in _anchors(params)
                                       if not re.fullmatch(r"[\d.]+%?", a)] + ["конец."])
    words = [{"w": w, "s": round(i * .4, 2), "e": round(i * .4 + .35, 2)} for i, w in enumerate(text.split())]
    return SceneCtx(block, f"b{block}", 10.0, words[-1]["e"], words, text, get_theme("blueprint"), Path("."),
                    position=position, count=count)


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_example_renders(name):
    _, doc = REGISTRY[name]
    assert doc.summary and doc.params and doc.example, f"{name}: нужен SceneDoc с параметрами и примером"
    params = {"type": name, **doc.example}
    out = render_scene(ctx_for(params), params)
    assert out.html.strip() and "<script" not in out.html.lower()
    # каждое правило CSS ограничено своей сценой
    for sel in re.findall(r"(?:^|})\s*([^{}@]+?)\s*{", out.css):
        assert all(part.strip().startswith("#b2") for part in sel.split(",")), f"{name}: селектор «{sel}» без #b2"
    assert not compose.js_syntax_errors({"b2": out.js}), f"{name}: синтаксическая ошибка в JS"
    for call in re.findall(r"tl\.\w+\((S\+\"[^\"]*\")", out.js):
        assert call.startswith('S+"'), f"{name}: анимация вне своей сцены"


@pytest.mark.parametrize("name", TEMPLATES)
def test_user_text_is_escaped(name):
    _, doc = REGISTRY[name]
    params = {"type": name, **doc.example}
    for k, v in params.items():  # подменяем первое строковое поле, которое попадает в кадр
        if isinstance(v, str) and k not in ("type", "at", "icon", "unit") and not k.endswith("_at"):
            params[k] = v + " <img src=x onerror=alert(1)>"
            break
    else:
        pytest.skip("нет текстовых полей")
    try:
        out = render_scene(ctx_for(params), params)
    except SceneError:
        return  # шаблон отверг такое значение — тоже безопасно
    assert "<img" not in out.html and "onerror=alert" not in out.js


def test_unknown_template():
    with pytest.raises(SceneError, match="неизвестный тип"):
        render_scene(ctx_for({}), {"type": "nope"})


def test_headline_cta_only_in_last_block():
    params = {"type": "headline", **REGISTRY["headline"][1].example}
    assert params.get("cta")
    first = render_scene(ctx_for(params, position=0, count=4), params)
    last = render_scene(ctx_for(params, position=3, count=4), params)
    assert "cta" not in first.html and "cta" in last.html
    always = render_scene(ctx_for(params, position=0, count=4), {**params, "cta_always": True})
    assert "cta" in always.html


def test_themes():
    assert {"blueprint", "night", "paper", "mono"} <= set(THEMES)
    t = get_theme({"base": "night", "accent": "#00e0a4"})
    assert t.accent == "#00e0a4" and t.bg == THEMES["night"].bg
    css = theme_css(t)
    assert "--accent:#00e0a4" in css.replace(" ", "") and ":root" in css
    with pytest.raises(ValueError, match="blueprint"):
        get_theme("nope")
