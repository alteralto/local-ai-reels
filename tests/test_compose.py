"""Сборка монтажа (reels.compose) и синтез звуков (reels.sfx) на примере «Поводок» — без GPU и моделей."""
from __future__ import annotations

import html as htmllib
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pytest
import scipy.io.wavfile as wavfile

from reels import compose, sfx
from reels import timing as tm
from reels.project import from_dict
from reels.scenes import REGISTRY, SceneDoc, SceneOut, esc, scene

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "example"
BLOCKS = [
    ("Крючок", "У каждого горожанина есть невидимый поводок, и длину ему отмерил город."),
    ("Факт", "Королевское общество здравоохранения Британии выяснило: каждый пятый выходит из дома реже, "
             "чем хочет, — боится не найти туалет."),
    ("Поворот", "Мы меряем город километрами и станциями метро, а у двоих из пяти больных он кончается "
                "у ближайшей уборной."),
    ("Панчлайн", "Генплан без туалетов — это карта с поводком: отправь тому, кто рисует ваш город."),
]


def _t_plain(ctx, params):
    return SceneOut(f'<div class="big tp"><span>{esc(params.get("title", ctx.block))}</span></div>',
                    f"{ctx.sel} .tp {{ top:300px; }}",
                    f'tl.fromTo(S+" .tp span",{{yPercent:110}},{{yPercent:0,duration:.4}},t+{ctx.at("50%"):.2f});')


def _t_broken(ctx, params):
    return SceneOut('<div class="big"><span>сломано</span></div>', "", "tl.fromTo(S+' .big', {opacity:0}")


@pytest.fixture(autouse=True)
def test_scenes():
    """Тестовые сцены регистрируются только на время теста — чтобы не попасть в каталог в других тестах."""
    scene("t_plain", SceneDoc("тестовая сцена: заголовок", {"title": "текст"}))(_t_plain)
    scene("t_broken", SceneDoc("тестовая сцена с синтаксической ошибкой в JS", {}))(_t_broken)
    yield
    REGISTRY.pop("t_plain", None)
    REGISTRY.pop("t_broken", None)


def make_project(tmp: Path, with_music: bool = False, **over):
    raw = {"title": "Поводок", "blocks": [{"name": n, "text": t, "scene": {"type": "t_plain", "title": n}}
                                          for n, t in BLOCKS],
           "source": "источник: RSPH, «Taking the P***», 2019", "endcard": "Город кончается там,\n*где нет туалета.*",
           "edits": {"linear": "1234", "fact": "2134"},
           "music": {"engine": "file", "file": "m.wav"} if with_music else {"engine": "none"}}
    raw.update(over)
    p = from_dict(raw, tmp)
    p.voice_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(EXAMPLE / "voice/voice.wav", p.master_wav)
    if with_music:
        p.music_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLE / "music/music.wav", p.music_wav)
    words = tm.aligned(json.loads((EXAMPLE / "voice/words.json").read_text(encoding="utf-8")), p.script)
    cuts = tm.block_cuts(words, tm.block_token_counts(p), p.cfg["cut_bias"])
    return p, {"cuts": cuts, "words": words}


class Doc(HTMLParser):
    """Собирает теги с атрибутами и проверяет вложенность."""
    VOID = {"meta", "link", "br", "img", "input", "hr", "source", "rect", "circle", "line", "path"}

    def __init__(self):
        super().__init__()
        self.tags, self.stack, self.errors, self.text = [], [], [], {}
        self._cap = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> при открытом {self.stack[-1:]}")
        else:
            self.stack.pop()


def parse(path: Path) -> Doc:
    d = Doc()
    d.feed(path.read_text(encoding="utf-8"))
    d.close()
    return d


def attrs(doc: Doc, tag: str, **match) -> list[dict]:
    return [a for t, a in doc.tags if t == tag and all(a.get(k) == v for k, v in match.items())]


def test_build_linear(tmp_path):
    p, timing = make_project(tmp_path)
    d = compose.build_edit(p, "linear", [1, 2, 3, 4], timing)
    for f in ("index.html", "voice.wav", "whoosh.wav", "hyperframes.json", "meta.json", "package.json"):
        assert (d / f).exists(), f
    assert not (d / "music.wav").exists()
    doc = parse(d / "index.html")
    assert not doc.errors and not doc.stack
    audio = attrs(doc, "audio")
    assert audio and all(a.get("id") for a in audio)
    assert len({a["id"] for a in audio}) == len(audio)
    vo = [a for a in audio if a["src"] == "voice.wav"]
    cuts = timing["cuts"]
    assert [float(a["data-media-start"]) for a in vo] == pytest.approx(cuts[:-1])
    assert sum(float(a["data-duration"]) for a in vo) == pytest.approx(cuts[-1] - cuts[0], abs=1e-3)
    assert len([a for a in audio if a["src"] == "whoosh.wav"]) == 3
    root = attrs(doc, "div", id="root")[0]
    total = float(root["data-duration"])
    assert total == pytest.approx(cuts[-1] + 3 * p.cfg["gap"] + p.cfg["tail"], abs=.01)
    card = attrs(doc, "div", id="endcard")[0]
    assert float(card["data-start"]) + float(card["data-duration"]) == pytest.approx(total, abs=.01)
    src = (d / "index.html").read_text(encoding="utf-8")
    assert "<em>где нет туалета.</em>" in src and "источник: RSPH, «Taking the P***», 2019" in src
    assert "gsap@3.14.2" in src and 'window.__timelines["main"] = tl' in src
    assert "--accent:" in src and "background-size: 40px 40px" in src


def test_captions_match_script(tmp_path):
    p, timing = make_project(tmp_path)
    src = (compose.build_edit(p, "linear", [1, 2, 3, 4], timing) / "index.html").read_text(encoding="utf-8")
    for b in p.blocks:
        body = src.split(f'<div id="b{b.index}" ')[1].split('<div class="cap2">')[1].split("</div>\n")[0]
        spans = [htmllib.unescape(s) for s in re.findall(r"<span data-s=\"[\d.]+\">(.*?)</span>", body)]
        assert spans == tm.script_tokens(b.text)
        for ch in re.findall(r'<div class="chunk"[^>]*>(.*?)</div>', body):
            n = len(re.findall("<span", ch))
            assert 1 <= n <= p.cfg["captions"]["max_words"]


def test_chunks_weak_words():
    ws = [{"w": w, "s": i, "e": i + 1} for i, w in enumerate("он держит их в городе крепко".split())]
    out = [" ".join(x["w"] for x in c) for c in compose.chunks(ws, max_chars=26, max_words=4)]
    assert out[0] == "он держит" and out[1].startswith("их в")


def test_try_catch_per_block(tmp_path):
    p, timing = make_project(tmp_path)
    src = (compose.build_edit(p, "linear", [1, 2, 3, 4], timing) / "index.html").read_text(encoding="utf-8")
    for b in (1, 2, 3, 4):
        assert f"const S='#b{b}'" in src
        assert f'console.error("сцена b{b}:", e)' in src
    assert src.count("try {") == 4 and len(re.findall(r"capAnim2\(S, t, [\d.]+\)", src)) == 4


def test_order_swap_changes_layout(tmp_path):
    p, timing = make_project(tmp_path)
    a = parse(compose.build_edit(p, "linear", [1, 2, 3, 4], timing) / "index.html")
    b = parse(compose.build_edit(p, "fact", [2, 1, 3, 4], timing) / "index.html")
    st = lambda doc, i: float(attrs(doc, "div", id=f"b{i}")[0]["data-start"])  # noqa: E731
    assert st(a, 1) == 0 and st(b, 2) == 0 and st(b, 1) > 0
    assert st(a, 3) == pytest.approx(st(b, 3))      # блоки 1 и 2 переставлены, 3 и 4 на месте
    mb = {x["id"]: float(x["data-media-start"]) for x in attrs(b, "audio", src="voice.wav")}
    assert mb["vo2"] == pytest.approx(timing["cuts"][1]) and mb["vo1"] == 0
    ids = [x["id"] for x in attrs(b, "audio", src="whoosh.wav")]
    assert ids == ["sfx-wh1", "sfx-wh3", "sfx-wh4"]


def test_music_and_no_endcard(tmp_path):
    p, timing = make_project(tmp_path, with_music=True, endcard="", source="",
                             music={"engine": "file", "file": "m.wav", "volume": .3})
    d = compose.build_edit(p, "linear", [1, 2, 3, 4], timing)
    assert (d / "music.wav").exists()
    doc = parse(d / "index.html")
    m = attrs(doc, "audio", id="music")[0]
    assert m["data-volume"] == "0.3" and m["data-fade-out"] == "1.6"
    assert not attrs(doc, "div", id="endcard") and 'class="src' not in (d / "index.html").read_text()
    total = float(attrs(doc, "div", id="root")[0]["data-duration"])
    last = attrs(doc, "div", id="b4")[0]
    assert float(last["data-start"]) + float(last["data-duration"]) == pytest.approx(total, abs=.01)
    assert compose.snapshot_times(p, [1, 2, 3, 4], timing)[-1] < total


def test_broken_scene_js_disabled(tmp_path):
    if not shutil.which("node"):
        pytest.skip("нет node")
    p, timing = make_project(tmp_path)
    p.blocks[1].scene = {"type": "t_broken"}
    src = (compose.build_edit(p, "linear", [1, 2, 3, 4], timing) / "index.html").read_text(encoding="utf-8")
    assert "JS сцены отключён" in src and "{opacity:0}\n" not in src
    js = tmp_path / "main.js"
    js.write_text(src.split("<script>\n")[1].split("</script>")[0], encoding="utf-8")
    assert subprocess.run(["node", "--check", str(js)]).returncode == 0   # весь скрипт страницы цел


def test_snapshot_and_poster(tmp_path):
    p, timing = make_project(tmp_path)
    order = [2, 1, 3, 4]
    pl, total = tm.place(order, timing["cuts"], p.cfg["gap"], p.cfg["tail"])
    ts = compose.snapshot_times(p, order, timing)
    assert len(ts) == 5 and ts == sorted(ts)
    for t, x in zip(ts, pl):
        assert x.start + .5 * x.length <= t < x.start + x.length
    assert ts[-1] == round(total - p.cfg["tail"] / 2, 1)
    assert all(round(t, 1) == t for t in ts)
    assert compose.poster_time(p, order, timing) == round(pl[0].length - .35, 1)
    p.cfg["poster"] = 3.5
    assert compose.poster_time(p, order, timing) == 3.5
    p.cfg["poster"] = {"edit": "fact", "at": 2}
    assert compose.poster_time(p, order, timing) == 2.0


def test_theme_paper_no_grid(tmp_path):
    p, timing = make_project(tmp_path, theme="paper")
    src = (compose.build_edit(p, "linear", [1, 2, 3, 4], timing) / "index.html").read_text(encoding="utf-8")
    assert "#f4efe4" in src and "background-size: 40px 40px" not in src


@pytest.mark.parametrize("fn", [sfx.whoosh, sfx.click, sfx.pop])
def test_sfx(tmp_path, fn):
    out = fn(tmp_path / "x.wav")
    sr, x = wavfile.read(out)
    assert sr == 48000 and x.dtype == np.float32 and x.ndim == 2 and x.shape[1] == 2
    assert .05 < len(x) / sr < 1 and 0.3 < np.abs(x).max() <= 0.51 and np.isfinite(x).all()
