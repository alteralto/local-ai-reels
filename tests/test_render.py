"""Тесты render.py, qa.py, finish.py.

Юнит-тесты — без сети и браузера (ffmpeg нужен для синтетических роликов).
Интеграционные — REELS_INTEGRATION=1: настоящий браузер (проверка вёрстки) и HyperFrames
(lint/snapshot/render проекта из REELS_HF_PROJECT — папки с index.html, где GSAP доступен без сети).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from reels import finish, qa, render
from reels.util import run, ff

HAS_FF = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ff = pytest.mark.skipif(not HAS_FF, reason="нужен ffmpeg")
integration = pytest.mark.skipif(os.environ.get("REELS_INTEGRATION") != "1", reason="REELS_INTEGRATION=1")


# --- render: разбор вывода, браузер, повторы ------------------------------------------------------

def test_parse_lint():
    out = "◆  Linting index.html\n  ⚠ something\n◇  0 error(s), 6 warning(s)\n"
    assert render.parse_lint(out)["errors"] == 0 and render.parse_lint(out)["warnings"] == 6
    assert render.parse_lint("◇  2 errors, 1 warning")["errors"] == 2


def test_browser_env(monkeypatch):
    monkeypatch.delenv("HYPERFRAMES_BROWSER_PATH", raising=False)
    assert render.browser() is None
    monkeypatch.setenv("HYPERFRAMES_BROWSER_PATH", "/x/chrome")
    assert render.browser() == "/x/chrome"


def test_find_browser(monkeypatch, tmp_path):
    exe = tmp_path / "chromium-1200" / "chrome-linux64" / "chrome"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(render, "_BROWSER_GLOBS", [str(tmp_path / "nope"), str(tmp_path / "chromium-*/chrome-linux*/chrome")])
    assert render.find_browser() == str(exe)
    monkeypatch.setattr(render, "_BROWSER_GLOBS", [str(tmp_path / "nope")])
    assert render.find_browser() is None


def test_env_hf_chrome(monkeypatch):
    monkeypatch.setenv("HF_CHROME", "/hs")
    env = render._env("/b")
    assert env["PRODUCER_HEADLESS_SHELL_PATH"] == "/hs" and env["HYPERFRAMES_BROWSER_PATH"] == "/b"
    assert env["HYPERFRAMES_NO_TELEMETRY"] == "1" and env["DO_NOT_TRACK"] == "1"


def test_hf_retry_on_403(monkeypatch, tmp_path):
    monkeypatch.delenv("HYPERFRAMES_BROWSER_PATH", raising=False)
    calls = []

    def fake(args, cwd, env, verbose, timeout):
        calls.append(env.get("HYPERFRAMES_BROWSER_PATH"))
        return (1, "Error: Failed to download Chrome: 403 Forbidden") if len(calls) == 1 else (0, "ok")

    monkeypatch.setattr(render, "_exec", fake)
    monkeypatch.setattr(render, "find_browser", lambda: "/found/chrome")
    assert render.hf(["lint"], tmp_path) == "ok"
    assert calls == [None, "/found/chrome"]


def test_hf_retry_on_nav_timeout_then_error(monkeypatch, tmp_path):
    monkeypatch.delenv("HYPERFRAMES_BROWSER_PATH", raising=False)
    n = []
    monkeypatch.setattr(render, "_exec", lambda *a: (n.append(1), (1, "TimeoutError: Navigation timeout of 60000 ms"))[1])
    with pytest.raises(render.ToolError):
        render.hf(["render"], tmp_path)
    assert len(n) == 2


# --- qa: формат и контактный лист ----------------------------------------------------------------

def test_format_issues():
    assert "замечаний нет" in qa.format_issues([])
    s = qa.format_issues([{"t": 2.0, "kind": "clipped", "severity": "error", "text": "Генплан без туал",
                           "rect": [80, 1210, 1100, 90], "detail": "обрезан"},
                          {"t": 14.0, "kind": "over_graphic", "severity": "warning", "text": "2 из 5",
                           "rect": [80, 640, 200, 80], "detail": "поверх карты"}])
    assert "ошибок 1, предупреждений 1" in s and "обрезан" in s and "поверх графики" in s


@needs_ff
def test_contact_sheet(tmp_path):
    pngs = []
    for i, c in enumerate(["red", "green", "blue", "white", "black"]):
        p = tmp_path / f"f{i}.png"
        run([ff(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color={c}:s=108x192", "-frames:v", "1", p])
        pngs.append(p)
    out = qa.contact_sheet(pngs, tmp_path / "sheet.png", cols=4, width=54, labels=[f"{i}.0 с" for i in range(5)])
    m = finish.parse_probe(json.loads(run([ff("ffprobe"), "-v", "error", "-show_entries", "stream=width,height",
                                           "-of", "json", out]).stdout))
    assert (m["width"], m["height"]) == (54 * 4, 96 * 2)
    one = qa.contact_sheet(pngs[:1], tmp_path / "one.png", width=54)
    assert one.is_file()


# --- finish: разбор, вердикт, отчёт --------------------------------------------------------------

EBUR = """[Parsed_ebur128_0 @ 0x1] t: 24.2 TARGET:-23 LUFS M: -20.1 S: -17.5 I: -16.9 LUFS
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -16.8 LUFS
    Threshold: -27.1 LUFS
"""


def test_parse_ebur128():
    assert finish.parse_ebur128(EBUR) == -16.8
    with pytest.raises(finish.ToolError):
        finish.parse_ebur128("no audio")


def test_parse_probe():
    d = {"streams": [{"codec_type": "video", "width": 1080, "height": 1920, "r_frame_rate": "30/1",
                      "avg_frame_rate": "30/1"}], "format": {"duration": "24.266667"}}
    assert finish.parse_probe(d) == {"width": 1080, "height": 1920, "fps": 30.0, "duration": 24.267}
    d["streams"][0]["avg_frame_rate"] = "30000/1001"
    assert finish.parse_probe(d)["fps"] == 29.97


def test_evaluate():
    good = {"width": 1080, "height": 1920, "fps": 30.0, "duration": 24.2, "lufs": -16.8}
    assert finish.evaluate(good) == []
    bad = {**good, "width": 720, "fps": 25.0, "lufs": -23.0, "ratio": 0.8, "layout_errors": 2}
    probs = finish.evaluate(bad, {"fps": 30})
    assert len(probs) == 5 and any("LUFS" in p for p in probs)


def test_verify_skipped_without_whisper(monkeypatch, tmp_path):
    monkeypatch.setattr(finish.importlib.util, "find_spec", lambda name: None)
    r = finish.verify(tmp_path / "x.mp4", "текст", {})
    assert r["ratio"] is None and "faster-whisper" in r["skipped"]


def test_write_report(tmp_path):
    proj = SimpleNamespace(out=tmp_path / "out", slug="demo", cfg={"title": "Поводок"})
    res = [{"edit": "linear", "file": str(tmp_path / "demo-linear.mp4"), "width": 1080, "height": 1920, "fps": 30.0,
            "duration": 24.27, "lufs": -16.8, "ratio": 0.99, "layout_issues": [], "ok": True, "problems": []},
           {"edit": "hook", "file": None, "lufs": None, "ratio": None, "skipped": "нет faster-whisper",
            "layout_issues": [{"severity": "error"}], "ok": False, "problems": ["громкость −30 LUFS"]}]
    j, m = finish.write_report(proj, res)
    assert json.loads(j.read_text(encoding="utf-8"))["edits"][0]["edit"] == "linear"
    md = m.read_text(encoding="utf-8")
    assert "| linear | demo-linear.mp4 | 24.27 | 1080×1920 | 30 | -16.8 | 99% | 0 | OK |" in md
    assert "1 ош. / 0 пред." in md and "пропущено" in md


@needs_ff
def test_poster_probe_loudness_check(tmp_path):
    """Синтетический ролик 1080×1920, 30 fps, тон −17 LUFS: постер, замеры, вердикт."""
    src = tmp_path / "r.mp4"
    run([ff(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=3", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=3", "-af", "loudnorm=I=-17:TP=-2", "-ar", "48000", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src])
    dst = finish.poster(src, 2.0, tmp_path / "final.mp4")
    m = finish.probe(dst)
    assert (m["width"], m["height"], m["fps"]) == (1080, 1920, 30.0) and abs(m["duration"] - 3) < 0.2
    assert abs(finish.loudness(dst) - (-17)) < 2.5
    # кадр 0 готового ролика = кадр с 2 с исходника
    f0, f2 = tmp_path / "f0.png", tmp_path / "f2.png"
    run([ff(), "-y", "-v", "error", "-i", dst, "-frames:v", "1", f0])
    run([ff(), "-y", "-v", "error", "-ss", "2", "-i", src, "-frames:v", "1", f2])
    diff = run([ff(), "-v", "info", "-i", f0, "-i", f2, "-lavfi", "psnr", "-f", "null", "-"]).stderr
    assert "average:inf" in diff or float(diff.split("average:")[1].split()[0]) > 30
    c = finish.check(dst, {"fps": 30})
    assert c["ok"], c["problems"]


# --- интеграция: проверка вёрстки в настоящем браузере --------------------------------------------

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ width:1080px; height:1920px; overflow:hidden; background:#123a7a; }}
#root {{ position:relative; width:1080px; height:1920px; font-family:sans-serif; color:#fff; }}
.scene {{ position:absolute; inset:0; overflow:hidden; }}
.big {{ position:absolute; left:80px; right:60px; font-weight:800; font-size:96px; line-height:1.02; overflow:hidden; }}
.big span {{ display:inline-block; }}
.a {{ top:260px; }} .b {{ top:360px; }}
.map {{ position:absolute; left:90px; top:520px; width:900px; height:900px; }}
.cap2 {{ position:absolute; left:60px; right:60px; bottom:260px; height:170px; text-align:center; }}
.cap2 .chunk {{ position:absolute; left:0; right:0; bottom:0; font-size:64px; }}
{extra}
</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="4">
  <div id="s1" class="scene clip" data-start="0" data-duration="2">
    <svg class="map" viewBox="0 0 900 900"><rect width="900" height="900" fill="#0b2350"/></svg>
    <div class="big a"><span>Невидимый</span></div>
    <div class="big b"><span>поводок.</span></div>
    <div class="big c" style="top:2000px"><span>ещё не выехал</span></div>
    <div class="cap2"><div class="chunk"><span>У каждого</span> <span>горожанина</span></div></div>
  </div>
  <div id="s2" class="scene clip" data-start="2" data-duration="2"><div class="big a"><span>Вторая сцена</span></div></div>
</div>
<script>window.__timelines = {{ main: {{ seek(t) {{ this.t = t; }} }} }};</script>
</body></html>"""


def _comp(tmp_path: Path, name: str, extra: str = "") -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "index.html").write_text(PAGE.format(extra=extra), encoding="utf-8")
    return d


@integration
def test_layout_check_clean_and_broken(tmp_path):
    good = qa.layout_check(_comp(tmp_path, "good"), [1.0, 3.0])
    assert qa.errors(good) == [], qa.format_issues(good)
    broken = qa.layout_check(_comp(tmp_path, "broken", extra=(
        ".a { font-size:200px; } .a span { white-space:nowrap; }"   # не влезает по ширине → обрезан и за кадром
        " .b { top:700px; font-size:120px; }"                         # над картой
        " #s2 .a { top:1500px; }")), [1.0, 3.0])                     # в зоне субтитров
    kinds = {(i["t"], i["kind"]) for i in broken}
    assert (1.0, "clipped") in kinds and (1.0, "out_of_frame") in kinds, qa.format_issues(broken)
    assert (1.0, "over_graphic") in kinds and (3.0, "captions_zone") in kinds, qa.format_issues(broken)
    # наложение: вторая строка поверх первой
    ov = qa.layout_check(_comp(tmp_path, "overlap", extra=".b { top:290px; }"), [1.0])
    assert any(i["kind"] == "overlap" and i["severity"] == "error" for i in ov), qa.format_issues(ov)


# --- интеграция: HyperFrames ---------------------------------------------------------------------

HF_PROJECT = os.environ.get("REELS_HF_PROJECT")
needs_project = pytest.mark.skipif(not HF_PROJECT or not Path(HF_PROJECT, "index.html").is_file(),
                                   reason="REELS_HF_PROJECT=<папка проекта HyperFrames>")


@pytest.fixture
def hf_project(tmp_path):
    d = tmp_path / "proj"
    shutil.copytree(HF_PROJECT, d, ignore=shutil.ignore_patterns("renders", "snapshots", "*.log"))
    return d


@integration
@needs_project
def test_hyperframes_lint_snapshot(hf_project):
    res = render.lint(hf_project)
    assert res["errors"] == 0, res["output"][-1500:]
    pngs = render.snapshot(hf_project, [2.0, 8.0])
    assert len(pngs) == 2 and all(p.stat().st_size > 1000 for p in pngs)
    sheet = qa.contact_sheet(pngs, hf_project / "sheet.jpg", labels=["2 с", "8 с"])
    assert sheet.is_file()


@integration
@needs_project
def test_hyperframes_render_finish(hf_project, tmp_path):
    mp4 = render.render(hf_project, tmp_path / "out" / "r.mp4", fps=30, quality="draft")
    final = finish.poster(mp4, 10.75, tmp_path / "out" / "final.mp4")
    c = finish.check(final, {"fps": 30})
    assert (c["width"], c["height"], c["fps"]) == (1080, 1920, 30.0)
    assert c["lufs"] is not None and abs(c["lufs"] + 17) < 2.5, c
