"""Несколько кадров в блоке, фирменная шапка, зерно, звуковые акценты, карточка-сцена, монтаж из части блоков."""
from __future__ import annotations

import re

import numpy as np
import pytest
import scipy.io.wavfile as wavfile
from test_compose import attrs, make_project, parse, test_scenes  # noqa: F401 — фикстура: сцены t_plain, t_broken

from reels import compose, sfx
from reels import timing as tm
from reels.project import ConfigError, from_dict
from reels.scenes import REGISTRY, SceneDoc, SceneOut, esc, scene

SEEN: list = []   # контексты, с которыми вызывалась тестовая сцена


def _t_shot(ctx, params):
    SEEN.append(ctx)
    files = {params["file_as"]: params["file"]} if params.get("file") else {}
    t0 = ctx.at(params.get("at"), .2)
    return SceneOut(f'<div class="big ts"><span>{esc(params.get("title", ctx.uid))}</span></div>',
                    f"{ctx.sel} .ts {{ top:400px; }}",
                    f'tl.fromTo(S+" .ts span",{{opacity:0}},{{opacity:1,duration:.3}},t+{t0:.2f});', files)


@pytest.fixture(autouse=True)
def shot_scene():
    SEEN.clear()
    scene("t_shot", SceneDoc("тестовая сцена кадра", {"title": "текст"}))(_t_shot)
    yield
    REGISTRY.pop("t_shot", None)


def block(src: str, uid: str) -> str:
    """HTML корня блока до следующего блока (или конца сцен)."""
    return src.split(f'<div id="{uid}" ')[1].split('\n  <div id="')[0]


def shots_project(tmp_path, **over):
    p, timing = make_project(tmp_path, **over)
    p.blocks[1].scene = {"type": "t_shot", "shots": [
        {"type": "t_shot", "title": "один"},
        {"type": "t_shot", "title": "два", "at": "каждый пятый", "transition": "slam",
         "sfx": [{"sound": "bang", "volume": .8}]},
        {"type": "t_shot", "title": "три", "at": "боится", "transition": "wipe"},
    ]}
    return p, timing


def test_shots_timing_and_visibility(tmp_path):
    p, timing = shots_project(tmp_path)
    d = compose.build_edit(p, "linear", [1, 2, 3, 4], timing)
    src = (d / "index.html").read_text(encoding="utf-8")
    doc = parse(d / "index.html")
    assert not doc.errors and not doc.stack
    bw = tm.block_words(timing["words"], timing["cuts"], 2)
    a2, a3 = tm.anchor(bw, "каждый пятый"), tm.anchor(bw, "боится")
    assert compose.shot_starts(p.blocks[1].scene, bw, 99) == [0.0, a2, a3]
    b2 = block(src, "b2")
    assert all(f'id="b2s{k}" class="shot"' in b2 for k in (1, 2, 3))
    assert 'class="flash"' in b2.split('id="b2s2"')[1].split('id="b2s3"')[0]   # slam — со вспышкой
    # контекст кадра: свой uid, начало — начало БЛОКА (якоря шаблонов), границы кадра — отдельно
    ctxs = {c.uid: c for c in SEEN}
    start = ctxs["b2s1"].start
    assert {c.start for u, c in ctxs.items() if u.startswith("b2s")} == {start}
    assert (ctxs["b2s2"].shot_start, ctxs["b2s2"].shot_end) == (a2, a3)
    assert ctxs["b2s3"].shot_end is None and ctxs["b2s1"].shot_start == 0
    assert ctxs["b2s1"].at(None, .2) == pytest.approx(.2) and ctxs["b2s3"].at(None, .2) == pytest.approx(a3 + .2)
    for sid in ("b2s1", "b2s2", "b2s3"):
        assert f"const S='#{sid}', t={compose.num(start)};" in src
    # видимость: кадр появляется на якоре, предыдущий прячется (у wipe — после шторки 0,25 с)
    assert f'tl.set("#b2s2", {{autoAlpha:1}}, t+{compose.num(a2)});' in src
    assert f'tl.set("#b2s1", {{autoAlpha:0}}, t+{compose.num(a2)});' in src
    assert f'tl.set("#b2s2", {{autoAlpha:0}}, t+{compose.num(a3 + .25)});' in src
    assert '"#b2s3", {clipPath:"inset(0% 100% 0% 0%)"}' in src and '"#b2s2", {scale:1.08}' in src
    assert "#b2s2 { visibility:hidden; opacity:0; }" in src and "#b2s3 { visibility:hidden; opacity:0; }" in src
    # кадры попадают в контрольные кадры snapshot
    ts = compose.snapshot_times(p, [1, 2, 3, 4], timing)
    pl = {x.block: x for x in tm.place([1, 2, 3, 4], timing["cuts"], p.cfg["gap"], p.cfg["tail"])[0]}
    for a, b in ((0, a2), (a2, a3)):
        assert any(pl[2].start + a < t < pl[2].start + b for t in ts)


def test_single_scene_unchanged(tmp_path):
    p, timing = make_project(tmp_path)
    src = (compose.build_edit(p, "linear", [1, 2, 3, 4], timing) / "index.html").read_text(encoding="utf-8")
    assert 'class="shot"' not in src and 'class="brand"' not in src and 'id="grain"' not in src
    assert src.count("try {") == 4 and len(re.findall(r"capAnim2\(S, t, [\d.]+\)", src)) == 4
    assert 'sfx-' not in src.replace("sfx-wh", "")


def test_shot_anchor_order_error(tmp_path):
    p, timing = make_project(tmp_path)
    p.blocks[1].scene = {"type": "t_shot", "shots": [{"type": "t_shot"}, {"type": "t_shot", "at": "боится"},
                                                     {"type": "t_shot", "at": "каждый пятый"}]}
    with pytest.raises(ValueError, match="кадр 3"):
        compose.build_edit(p, "linear", [1, 2, 3, 4], timing)


def test_shots_in_yaml(tmp_path):
    p = from_dict({"blocks": [
        {"text": "Раз два три.", "shots": [{"type": "t_shot"}, {"type": "t_shot", "at": "два", "transition": "fade"}],
         "sfx": [{"at": "три", "sound": "knock"}], "role": "hook"},
        {"text": "Четыре.", "scene": {"shots": ["t_shot", {"type": "t_shot", "sfx": "pop"}]}},
    ], "edits": {"linear": "12"}}, tmp_path)
    b1, b2 = p.blocks
    assert b1.scene["type"] == "t_shot" and len(b1.shots) == 2 and b1.role == "hook"
    assert b1.sfx == [{"at": "три", "sound": "knock", "volume": .6}]
    assert b2.shots[1]["sfx"] == [{"at": None, "sound": "pop", "volume": .6}]
    for bad, msg in [({"shots": [{"type": "t_shot", "transition": "spin"}]}, "transition"),
                     ({"shots": []}, "непустой"),
                     ({"type": "t_shot", "sfx": [{"sound": "boom"}]}, None)]:
        raw = {"text": "Раз.", "scene": bad}
        if msg is None:
            raw = {"text": "Раз.", "sfx": [{"sound": "boom"}]}
            msg = "sound"
        with pytest.raises(ConfigError, match=msg):
            from_dict({"blocks": [raw], "edits": {"linear": "1"}}, tmp_path)


def test_captions_none(tmp_path):
    p, timing = make_project(tmp_path, captions={"style": "none"})
    src = (compose.build_edit(p, "linear", [1, 2, 3, 4], timing) / "index.html").read_text(encoding="utf-8")
    assert 'class="cap2"' not in src and not re.findall(r"capAnim2\(S, t, [\d.]+\)", src)
    assert len(re.findall(r"outro\(S, [\d.]+\)", src)) == 4
    with pytest.raises(ConfigError, match="captions.style"):
        make_project(tmp_path, captions={"style": "big"})


def test_brand_header_and_partners(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    (tmp_path / "assets/p.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    p, timing = shots_project(tmp_path, brand={
        "logo": "assets/logo.svg", "name": "Департамент\nградостроительной политики",
        "partners": ["ASADOV", {"logo": "assets/p.png", "name": "Партнёр"}, "РАНХиГС"]})
    p.blocks[3].brand = False
    d = compose.build_edit(p, "linear", [1, 2, 3, 4], timing)
    src = (d / "index.html").read_text(encoding="utf-8")
    assert (d / "brand-logo.svg").is_file() and (d / "partner-2.png").is_file()
    for uid in ("b1", "b2", "b3"):
        b = block(src, uid)
        assert '<div class="brand"><img src="brand-logo.svg"' in b and "Департамент<br>" in b
        assert '<div class="rule"></div>' in b and "has-brand" in b.split(">")[0]
    assert 'class="brand"' not in block(src, "b4")
    card = src.split('<div id="endcard" ')[1].split("</div>\n  </div>")[0]
    assert 'class="brand"' in card and 'class="partners"' in card
    assert "<span>ASADOV</span>" in card and 'src="partner-2.png"' in card
    assert 'S+" > :not(.cap2):not(.brand)"' in src   # уход сцены не трогает шапку
    with pytest.raises(ConfigError, match="brand"):
        make_project(tmp_path, brand={"color": "red"})


def test_endcard_scene(tmp_path):
    p, timing = make_project(tmp_path, endcard={"type": "t_shot", "title": "финал",
                                                "sfx": [{"at": .3, "sound": "stamp"}]})
    d = compose.build_edit(p, "linear", [1, 2, 3, 4], timing)
    doc = parse(d / "index.html")
    src = (d / "index.html").read_text(encoding="utf-8")
    end = attrs(doc, "div", id="end")
    assert end and not attrs(doc, "div", id="endcard") and "финал" in src
    total = float(attrs(doc, "div", id="root")[0]["data-duration"])
    tail = p.cfg["tail"]
    assert float(end[0]["data-start"]) == pytest.approx(total - tail) and f"const S='#end', t={compose.num(total - tail)}" in src
    a = attrs(doc, "audio", src="sfx-stamp.wav")
    assert len(a) == 1 and float(a[0]["data-start"]) == pytest.approx(total - tail + .3, abs=1e-3)
    assert attrs(doc, "audio", id="sfx-whend")
    # карточка из кадров
    p2, timing = make_project(tmp_path, endcard={"shots": [{"type": "t_shot"}, {"type": "t_shot", "at": .8}]})
    src = (compose.build_edit(p2, "linear", [1, 2, 3, 4], timing) / "index.html").read_text(encoding="utf-8")
    assert 'id="ends1"' in src and 'id="ends2"' in src and 'tl.set("#ends2", {autoAlpha:1}, t+0.8);' in src


def test_sfx_cues(tmp_path):
    p, timing = shots_project(tmp_path)
    p.blocks[1].sfx = [{"at": "каждый пятый", "sound": "knock", "volume": .6},
                       {"at": "каждый пятый+0.1", "sound": "knock", "volume": .5}]
    d = compose.build_edit(p, "linear", [1, 2, 3, 4], timing)
    doc = parse(d / "index.html")
    audio = attrs(doc, "audio")
    assert all(a.get("id") for a in audio) and len({a["id"] for a in audio}) == len(audio)
    knocks = attrs(doc, "audio", src="sfx-knock.wav")
    bangs = attrs(doc, "audio", src="sfx-bang.wav")
    assert len(knocks) == 2 and len(bangs) == 1
    assert (d / "sfx-knock.wav").is_file() and (d / "sfx-bang.wav").is_file()
    pl = {x.block: x for x in tm.place([1, 2, 3, 4], timing["cuts"], p.cfg["gap"], p.cfg["tail"])[0]}
    a2 = tm.anchor(tm.block_words(timing["words"], timing["cuts"], 2), "каждый пятый")
    assert float(knocks[0]["data-start"]) == pytest.approx(pl[2].start + a2, abs=1e-3)
    assert float(bangs[0]["data-start"]) == pytest.approx(pl[2].start + a2, abs=1e-3)  # sfx кадра — с начала кадра
    assert bangs[0]["data-volume"] == "0.8"
    # одновременные звуки — на разных дорожках, дорожки акцентов не пересекаются со вжухами (5)
    tracks = [a["data-track-index"] for a in knocks + bangs]
    assert len(set(tracks)) == 3 and all(int(t) >= 6 for t in tracks)
    assert float(knocks[0]["data-duration"]) == pytest.approx(sfx.duration(d / "sfx-knock.wav"))


def test_grain_overlay(tmp_path):
    p, timing = make_project(tmp_path, theme={"base": "blueprint", "grain": .3})
    d = compose.build_edit(p, "linear", [1, 2, 3, 4], timing)
    src = (d / "index.html").read_text(encoding="utf-8")
    assert '<div id="grain" style="opacity:0.3"></div>' in src and "mix-blend-mode:overlay" in src
    assert 'tl.fromTo("#grain"' in src
    png = (d / "grain.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png[16:20], "big") == 512
    # grain поверх сцен: после всех блоков в разметке
    assert src.index('id="grain"') > src.index('id="b4"')


def test_scene_files_copied(tmp_path):
    img = tmp_path / "assets" / "pic.svg"
    img.parent.mkdir()
    img.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    p, timing = make_project(tmp_path)
    p.blocks[0].scene = {"type": "t_shot", "file": "assets/pic.svg", "file_as": "b1-pic.svg"}
    p.blocks[2].scene = {"shots": [{"type": "t_shot", "file": str(img), "file_as": "media/b3.svg"},
                                   {"type": "t_shot", "at": "50%"}]}
    d = compose.build_edit(p, "linear", [1, 2, 3, 4], timing)
    assert (d / "b1-pic.svg").read_bytes() == img.read_bytes() and (d / "media/b3.svg").is_file()
    p.blocks[0].scene = {"type": "t_shot", "file": "assets/pic.svg", "file_as": "../x.svg"}
    with pytest.raises(ValueError, match="«..»"):
        compose.build_edit(p, "linear", [1, 2, 3, 4], timing)


def test_edit_subset_of_blocks(tmp_path):
    p, timing = make_project(tmp_path, edits={"hook-a": "1234", "hook-b": "234", "short": "42"})
    assert p.edits["hook-b"] == [2, 3, 4]
    for name, order in p.edits.items():
        d = compose.build_edit(p, name, order, timing)
        doc = parse(d / "index.html")
        vo = attrs(doc, "audio", src="voice.wav")
        assert [a["id"] for a in vo] == [f"vo{b}" for b in order]
        ts = compose.snapshot_times(p, order, timing)
        pl, total = tm.place(order, timing["cuts"], p.cfg["gap"], p.cfg["tail"])
        assert len(ts) >= len(order) and max(ts) < total
        assert 0 < compose.poster_time(p, order, timing) < pl[0].length
    for edits, msg in [({"x": "125"}, "нет таких блоков: 5"), ({"x": "1224"}, "ровно раз"), ({"x": ""}, "хотя бы один")]:
        with pytest.raises(ConfigError, match=msg):
            make_project(tmp_path, edits=edits)


def test_own_edits_replace_default(tmp_path):
    raw = {"blocks": [{"text": t} for t in ("Раз.", "Два.", "Три.", "Четыре.", "Пять.")]}
    p = from_dict({**raw, "edits": {"hook-a": "145", "hook-b": "245"}}, tmp_path)
    assert p.edits == {"hook-a": [1, 4, 5], "hook-b": [2, 4, 5]}   # без linear по умолчанию
    assert from_dict(raw, tmp_path).edits == {"linear": [1, 2, 3, 4, 5]}
    assert from_dict({**raw, "edits": {}}, tmp_path).edits == {"linear": [1, 2, 3, 4, 5]}
    assert from_dict({"blocks": ["Раз.", "Два."]}, tmp_path).edits == {"linear": [1, 2]}


@pytest.mark.parametrize("name", sorted(sfx.SOUNDS))
def test_sfx_sounds(tmp_path, name):
    out = sfx.make(name, tmp_path / f"{name}.wav")
    sr, x = wavfile.read(out)
    assert sr == 48000 and x.dtype == np.float32 and x.ndim == 2 and x.shape[1] == 2
    assert .05 < len(x) / sr < 1 and 0.3 < np.abs(x).max() <= 0.71 and np.isfinite(x).all()
    assert np.abs(x[-10:]).max() < .2   # без щелчка в конце
    with pytest.raises(ValueError, match="неизвестный звук"):
        sfx.make("boom", tmp_path / "b.wav")
