"""Шаблон illustration и городские пропы: каждый проп рисуется, действия дают корректный JS, слои проверяются."""
import re

import pytest

from reels import compose
from reels.scenes import REGISTRY, SceneError, render_scene
from reels.scenes.props import PROPS, render_prop
from reels.scenes.props.city import color
from test_scenes import ctx_for

CITY = ["moon", "sun", "stars", "cloud", "skyline", "building-classic", "building-brick", "panel-block", "tower",
        "streetlamp", "ground", "tree"]


@pytest.mark.parametrize("name", CITY)
def test_city_prop_renders(name):
    _, doc = PROPS[name]
    assert doc.summary and isinstance(doc.params, dict)
    out = render_prop(name, {}, "b2-l0")
    assert out.svg.startswith("<svg") and out.svg.endswith("</svg>") and out.w > 0 and out.h > 0
    ids = re.findall(r'id="([^"]+)"', out.svg)
    assert all(i.startswith("b2-l0") for i in ids), f"{name}: id без префикса uid: {ids}"
    assert sorted(out.actions) == sorted(doc.actions), f"{name}: действия в PropDoc и PropOut расходятся"
    js = {f"a{i}": fn_("#b2 .l0", 10.5) for i, fn_ in enumerate(out.actions.values())}
    assert not compose.js_syntax_errors(js), f"{name}: синтаксическая ошибка в JS действия"
    assert all(re.match(r"^tl\.(fromTo|to|set)\(", x) for x in js.values())


def test_prop_variants():
    for seed in range(12):
        for d in (.5, 1, 2):
            assert render_prop("skyline", {"seed": seed, "density": d, "lit": True}, "u").svg
    assert "mask" in render_prop("moon", {"crescent": True}, "u").svg
    assert render_prop("building-brick", {"floors": 6, "lit": .5}, "u").h > render_prop("building-brick", {}, "u").h


def test_color_validation():
    assert color("accent", "x") == "var(--accent)" and color("#cc1418", "x") == "#cc1418" and color(None, "d") == "d"
    with pytest.raises(ValueError):
        color('red" onload="alert(1)', "x")
    with pytest.raises(ValueError):
        render_prop("moon", {"color": "url(javascript:1)"}, "u")


def _params(**over):
    return {"type": "illustration", **REGISTRY["illustration"][1].example, **over}


def test_layers_positions_and_actions():
    p = _params()
    out = render_scene(ctx_for(p), p)
    # ground + 5 слоёв, у каждого свой класс l{i} и свой SVG с uid-префиксом
    assert out.html.count('class="lay ') == 6 and 'class="b2-l1"' in out.html
    assert "tl.fromTo(\"#b2 .l5 .b2-l5-lit\"" in out.js       # действие lights кирпичного дома
    assert 'S+" .cam"' in out.js and "grain" in out.html
    assert not compose.js_syntax_errors({"b2": out.js})


def test_layer_errors():
    with pytest.raises(SceneError, match="layers"):
        render_scene(ctx_for({}), {"type": "illustration"})
    with pytest.raises(SceneError, match="неизвестный проп"):
        render_scene(ctx_for({}), {"type": "illustration", "layers": [{"prop": "nope"}]})
    with pytest.raises(SceneError, match="нет действия"):
        render_scene(ctx_for({}), {"type": "illustration", "layers": [{"prop": "moon", "actions": [{"do": "open"}]}]})
    with pytest.raises(SceneError, match="enter"):
        render_scene(ctx_for({}), {"type": "illustration", "layers": [{"prop": "moon", "enter": "spin"}]})


def test_shadow_float_parallax_and_anchor():
    p = {"type": "illustration", "grain": 0, "layers": [
        {"prop": "panel-block", "x": 300, "y": 1400, "w": 300, "shadow": True, "float": True, "parallax": .5,
         "anchor": "center", "flip": True, "rotate": -3}]}
    out = render_scene(ctx_for(p), p)
    assert out.html.count("<svg") == 2 and "grain" not in out.html            # тень — копия пропа, зерно выключено
    assert "scaleX(-1) rotate(-3deg)" in out.css and "repeat:" in out.js and "x:-45" in out.js
    h = 300 * render_prop("panel-block", {}, "x").h / render_prop("panel-block", {}, "x").w
    assert f"top:{1400 - h / 2:.1f}px" in out.css
