"""Плакатные наклейки (stickers, tag, cover) и темы poster*."""
import re
from pathlib import Path

import pytest

from reels.scenes import SceneCtx, SceneError, render_scene
from reels.scenes.stickers import _em
from reels.themes import THEMES, get_theme, theme_css


def ctx(text="Вступление что прячет городская кабинка тук-тук конец", theme="poster"):
    words = [{"w": w, "s": round(i * .4, 2), "e": round(i * .4 + .35, 2)} for i, w in enumerate(text.split())]
    return SceneCtx(2, "b2", 0.0, words[-1]["e"], words, text, get_theme(theme), Path("."))


def boxes(out):
    """(left, top, font-size) строк из CSS сцены и (left, top, width, height) стопки."""
    lines = [tuple(float(x) for x in m) for m in
             re.findall(r"\.ln\d+\{left:(-?\d+)px;top:(-?\d+)px;font-size:(\d+)px", out.css)]
    stk = re.search(r"\.stk\{left:(-?\d+)px;top:(-?\d+)px;width:(\d+)px;height:(\d+)px", out.css)
    return lines, tuple(float(x) for x in stk.groups())


def test_poster_themes_and_sticker_vars():
    assert {"poster", "poster-cream", "poster-night", "blueprint", "night", "paper", "mono"} <= set(THEMES)
    p = get_theme("poster")
    assert p.bg.lower() == "#d7141a" and not p.grid and "Montserrat" in p.fonts_url
    css = theme_css(p)
    for var in ("--sticker-bg", "--sticker-ink", "--label-bg", "--label-ink", "--accent-bg", "--accent-ink"):
        assert var + ":" in css
    assert "--sticker-bg:" in theme_css(get_theme("blueprint"))      # старые темы получают значения по умолчанию
    if hasattr(p, "grain"):
        assert p.grain > 0


def test_stickers_slam_and_timing():
    out = render_scene(ctx(), {"type": "stickers", "lines": [{"text": "Что прячет", "at": "что прячет"},
                                                             "городская", "кабинка?"]})
    assert out.html.count('class="ln ') == 3
    starts = [float(x) for x in re.findall(r"scale:1\.32.*?t\+([\d.]+)\);", out.js)]
    assert len(starts) == 3
    assert starts[1] == pytest.approx(starts[0] + .18) and starts[2] == pytest.approx(starts[0] + .36)
    assert "rotate(-5deg)" in out.css


@pytest.mark.parametrize("pos", ["top", "center", "bottom", 900])
def test_stickers_stay_in_frame(pos):
    lines = ["Очень длинная строка наклейки", "ещё", {"text": "мелкая бирка", "size": "S", "style": "black",
                                                       "shift": 2000}, "ТУАЛЕТ — ТОЖЕ АРХИТЕКТУРА"] * 2
    out = render_scene(ctx(), {"type": "stickers", "lines": lines, "position": pos, "tilt": -8})
    ln, (sx, sy, sw, sh) = boxes(out)
    assert sx >= 40 and sx + sw <= 1040
    ext = sw / 2 * 0.14 + 1                          # поворот на 8° поднимает и опускает углы стопки
    assert sy - ext >= 160 and sy + sh + ext <= 1450
    for left, _, fs in ln:
        assert 0 <= left <= sw and fs >= 20


def test_stickers_fit_widest_line():
    out = render_scene(ctx(), {"type": "stickers", "lines": ["АРХИТЕКТУРНОГО ОБРАЗОВАНИЯ"]})
    (_, _, fs), = boxes(out)[0]
    assert fs * (_em("АРХИТЕКТУРНОГО ОБРАЗОВАНИЯ", True) + .4) <= 900 + 1


@pytest.mark.parametrize("bad", [{"lines": []}, {"lines": [{"text": "x", "style": "neon"}]},
                                 {"lines": ["x"], "position": "left"}, {"lines": [{"text": "x", "size": "XL"}]},
                                 {"lines": ["x"], "stagger": "wave"}])
def test_stickers_bad_params(bad):
    with pytest.raises(SceneError):
        render_scene(ctx(), {"type": "stickers", **bad})


def test_stickers_escape():
    out = render_scene(ctx(), {"type": "stickers", "lines": ["<b>x</b> *акцент*"]})
    assert "<b>" not in out.html and "<em>акцент</em>" in out.html


def test_tag_clamped_and_wiggle():
    out = render_scene(ctx(), {"type": "tag", "text": "тук-тук", "x": 2000, "y": -50, "at": "тук-тук", "wiggle": 3})
    m = re.search(r"\.stk\{left:(-?\d+)px;top:(-?\d+)px;width:(\d+)px", out.css)
    x, y, w = (float(v) for v in m.groups())
    assert 40 <= x and x + w <= 1040 and y >= 160
    assert "repeat:5" in out.js and "yoyo:true" in out.js and "black" in out.html


def test_cover_order_and_anchors():
    text = "Вступление открытый город ассинтезия города теневая архитектура конец"
    out = render_scene(ctx(text), {"type": "cover", "title": ["Открытый", "город"], "sub": "фестиваль",
                                   "label": "воркшоп", "title2": ["Ассинтезия"], "tags": ["теневая архитектура"],
                                   "at": "открытый", "title2_at": "ассинтезия", "tags_at": "теневая"})
    assert out.html.count('class="ln ') == 6
    ts = [float(x) for x in re.findall(r"t\+([\d.]+)\);", out.js)]
    assert ts == sorted(ts)
    assert ts[4] == pytest.approx(ctx(text).at("ассинтезия")) and ts[5] == pytest.approx(ctx(text).at("теневая"))
