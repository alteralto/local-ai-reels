"""Пропсы для шаблона illustration: рисуются с параметрами по умолчанию, id/классы с префиксом uid, JS действий
синтаксически корректен, у каждого есть PropDoc."""
import re

import pytest

from reels.compose import js_syntax_errors
from reels.scenes.props import PROPS, PropDoc, PropOut, render_prop

PEOPLE = ["kiosk", "person", "spotlight", "scorecard", "stamp", "burst", "phone", "laptop", "pointer", "sparkle",
          "confetti"]


@pytest.mark.parametrize("name", PEOPLE)
def test_prop_renders_with_defaults(name):
    assert name in PROPS
    fn, doc = PROPS[name]
    assert isinstance(doc, PropDoc) and doc.summary and doc.actions
    out = render_prop(name, {}, "a1")
    assert isinstance(out, PropOut)
    assert out.svg.startswith("<svg") and out.svg.rstrip().endswith("</svg>")
    assert re.search(r'viewBox="0 0 [\d.]+ [\d.]+"', out.svg)
    assert out.w > 0 and out.h > 0 and out.actions
    assert "Math.random" not in out.svg + out.css


@pytest.mark.parametrize("name", PEOPLE)
def test_ids_and_classes_are_uid_prefixed(name):
    a, b = render_prop(name, {}, "pA"), render_prop(name, {}, "pB")
    for uid, out in (("pA", a), ("pB", b)):
        for i in re.findall(r'\bid="([^"]+)"', out.svg):
            assert i.startswith(uid + "-"), i
        for cls in re.findall(r'\bclass="([^"]+)"', out.svg):
            for c in cls.split():
                assert c.startswith(uid + "-"), c
        for ref in re.findall(r'url\(#([^)]+)\)', out.svg):
            assert ref.startswith(uid + "-"), ref
    ids_a = set(re.findall(r'\bid="([^"]+)"', a.svg))
    ids_b = set(re.findall(r'\bid="([^"]+)"', b.svg))
    assert not ids_a & ids_b


@pytest.mark.parametrize("name", PEOPLE)
def test_actions_emit_valid_js(name):
    out = render_prop(name, {}, "z9")
    snippets = {}
    for act, fn in out.actions.items():
        js = fn("#root-z9", 1.25)
        assert "tl." in js and "Math.random" not in js
        assert "#root-z9 .z9-" in js
        snippets[f"{name}.{act}"] = "(function(tl){\n" + js + "\n})"
    assert js_syntax_errors(snippets) == {}


def test_params_change_output():
    k = render_prop("kiosk", {"label": "МЖ", "door_text": "7", "color": "#123456", "open": True, "eyes": True}, "k")
    assert "МЖ" in k.svg and ">7<" in k.svg and "#123456" in k.svg and "matrix(" in k.svg
    s = render_prop("scorecard", {"title": "Оценки", "items": ["раз", "два", "три"], "marks": [3, 4, 5]}, "s")
    assert "Оценки" in s.svg and "РАЗ" in s.svg
    assert {"stamp_1", "stamp_2", "stamp_3", "stamp_all", "big_stamp"} <= set(s.actions)
    assert "stamp_4" not in s.actions
    ph = render_prop("phone", {"src": "b1-shot.png", "taps": [[.2, .3], [.8, .9]]}, "ph")
    assert 'href="b1-shot.png"' in ph.svg and {"tap_1", "tap_2"} <= set(ph.actions)
    pe = render_prop("person", {"hold": "phone", "holding": True}, "pe")
    assert "pe-held" in pe.svg
    b = render_prop("burst", {"text": "<БАХ>", "style": "star"}, "b")
    assert "&lt;БАХ&gt;" in b.svg


def test_deterministic():
    for name in PEOPLE:
        assert render_prop(name, {}, "d").svg == render_prop(name, {}, "d").svg


def test_symbolic_time_positions():
    js = render_prop("kiosk", {}, "q").actions["jump"]("#r", "t+0.5")
    assert "(t+0.5)+" in js
    assert js_syntax_errors({"x": "(function(tl,t){\n" + js + "\n})"}) == {}
