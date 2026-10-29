"""Конвейер на подменённых этапах: кэш, контрольные точки, монтажи, пакетный режим."""
import json
import shutil
import sys
import types
from collections import Counter
from pathlib import Path

import pytest
import yaml

import reels
from conftest import BLOCKS, ROOT
from reels import pipeline
from reels.cli import main
from reels.project import load

CALLS = Counter()


def _fake_modules(monkeypatch):
    def mod(name, **fns):
        m = types.SimpleNamespace(**fns)
        monkeypatch.setitem(sys.modules, f"reels.{name}", m)
        monkeypatch.setattr(reels, name, m, raising=False)

    def hit(name, fn):
        def wrapper(*a, **k):
            CALLS[name] += 1
            return fn(*a, **k)
        return wrapper

    def accent(p):
        out = p.voice_dir / "accents.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"master": p.script, "ref": "x"}, ensure_ascii=False), encoding="utf-8")
        return out

    def synthesize(p):
        shutil.copy(ROOT / "example/voice/voice.wav", p.master_wav)
        (p.voice_dir / "choice.json").write_text('{"file": "t.wav", "ratio": 1.0, "ok": true}')
        return {"file": "t.wav", "ratio": 1.0, "ok": True}

    def timestamps(p):
        shutil.copy(ROOT / "example/voice/words.json", p.words_json)
        return p.words_json

    def make_music(p, seconds):
        p.music_dir.mkdir(parents=True, exist_ok=True)
        p.music_wav.write_bytes(b"RIFF")
        return p.music_wav

    def build_edit(p, edit, order, timing):
        d = p.edit_dir(edit)
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(f"{order} {timing['cuts']} {[b.text for b in p.blocks]}", encoding="utf-8")
        return d

    def touch(path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")
        return Path(path)

    def write_report(p, results):
        return touch(p.out / "report.json"), touch(p.out / "report.md")

    mod("voice", accent=hit("accent", accent), synthesize=hit("voice", synthesize),
        timestamps=hit("timestamps", timestamps))
    mod("music", make_music=hit("music", make_music))
    mod("compose", build_edit=hit("build", build_edit), snapshot_times=lambda p, o, t: [1.0, 2.0],
        poster_time=lambda p, o, t: 1.0)
    mod("render", lint=lambda d: {"errors": 0, "warnings": 0},
        snapshot=hit("snapshot", lambda d, times: [touch(d / "snapshots" / "f.png")]),
        render=hit("render", lambda d, out, fps, quality: touch(out)))
    mod("qa", layout_check=lambda d, times: [], contact_sheet=lambda pngs, out: touch(out),
        format_issues=lambda issues: "")
    mod("finish", poster=hit("finish", lambda src, at, dst: touch(dst)), check=lambda mp4, expect: {"ok": True},
        verify=lambda mp4, script, cfg: {"ratio": 1.0}, write_report=write_report)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    CALLS.clear()
    _fake_modules(monkeypatch)
    cfg = {"title": "t", "source": "s",
           "blocks": [{"text": t, "scene": {"type": "headline", "lines": ["x"]}} for t in BLOCKS],
           "edits": {"linear": 1234, "suspense": 1324}}
    d = tmp_path / "reel"
    d.mkdir()
    (d / "reel.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return d


def test_full_run_then_cached(proj):
    pipeline.run(load(proj), yes=True, interactive=False)
    p = load(proj)
    assert p.out_mp4("linear").exists() and p.out_mp4("suspense").exists()
    timing = json.loads(p.timing_json.read_text(encoding="utf-8"))
    assert timing["cuts"] == pytest.approx([0.0, 4.2, 11.45, 17.6, 22.4], abs=0.02)
    first = dict(CALLS)
    assert first["build"] == 2 and first["render"] == 2 and first["voice"] == 1
    pipeline.run(load(proj), yes=True, interactive=False)
    assert dict(CALLS) == first, "второй прогон без изменений ничего не пересчитывает"


def test_gates_stop_and_approve(proj):
    with pytest.raises(pipeline.GateWait) as g:
        pipeline.run(load(proj), interactive=False)
    assert g.value.gate == "script" and "59 / 60" in g.value.summary
    pipeline.approve(load(proj), "script")
    with pytest.raises(pipeline.GateWait) as g:
        pipeline.run(load(proj), interactive=False)
    assert g.value.gate == "accents"
    assert CALLS["voice"] == 0, "до подтверждения ударений озвучка не запускается"


def test_change_text_invalidates_downstream(proj):
    pipeline.run(load(proj), yes=True, interactive=False)
    before = dict(CALLS)
    cfg = yaml.safe_load((proj / "reel.yaml").read_text(encoding="utf-8"))
    cfg["endcard"] = "Новый *финал*"                 # меняется только сборка, не озвучка
    (proj / "reel.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    pipeline.run(load(proj), yes=True, interactive=False)
    assert CALLS["voice"] == before["voice"] and CALLS["build"] == before["build"] + 2
    assert CALLS["render"] == before["render"] + 2


def test_only_and_edit_filter(proj):
    pipeline.run(load(proj), yes=True, interactive=False, edits=["linear"])
    assert CALLS["build"] == 1 and not load(proj).out_mp4("suspense").exists()
    pipeline.run(load(proj), yes=True, interactive=False, edits=["linear"], only="render")
    assert CALLS["render"] == 1, "--only учитывает кэш"
    pipeline.run(load(proj), yes=True, interactive=False, edits=["linear"], only="render", force=True)
    assert CALLS["render"] == 2 and CALLS["build"] == 1


def test_status_and_cli(proj, capsys):
    assert main(["run", str(proj), "--no-input"]) == 3          # остановка на контрольной точке
    assert "approve" in capsys.readouterr().out
    assert main(["batch", str(proj), "--yes"]) == 0
    assert main(["status", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "render@linear" in out and "done" in out
    assert main(["status", "--json", str(proj), str(proj / "missing")]) == 0
    js = json.loads(capsys.readouterr().out)
    assert js[0]["gates"][0] == {"gate": "script", "stage": "check", "status": "approved"}
    assert js[0]["outputs"]["linear"]["mp4"].endswith("reel-linear.mp4") and js[1]["error"]


def test_new_from_example(tmp_path):
    dst = tmp_path / "copy"
    assert main(["new", str(dst), "--from", str(ROOT / "examples/povodok-templates")]) == 0
    p = load(dst)
    for key in ("file", "words"):
        f = p.path(p.cfg["voice"].get(key))
        assert f is None or (f.exists() and f.is_relative_to(dst))
    assert main(["new", str(tmp_path / "blank")]) == 0
    assert load(tmp_path / "blank").blocks
